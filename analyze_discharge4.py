from __future__ import annotations

import argparse
import csv
import gc
import gzip
import math
import re
import struct
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.signal import find_peaks


# Centralized protocol assumptions inferred from the available data.
# The first numeric field in the file name is also present in the packet header
# as the feature/sensor type. The second numeric field is a channel under that
# type. From the packet header and waveform sizes:
#   1: UHF / 特高频, high sample-count waveform packets.
#   2: HF / 高频, medium sample-count waveform packets.
#   4: ultrasonic / 超声, acoustic/low-band waveform packets.
SIGNAL_TYPES = {
    "1": {"key": "uhf", "zh": "特高频", "en": "UHF", "protocol": "UHF"},
    "2": {"key": "hf", "zh": "高频", "en": "HF", "protocol": "HFCT_SCREEN"},
    "4": {"key": "ultrasonic", "zh": "超声", "en": "Ultrasonic", "protocol": "ACOUSTIC"},
}
SIGNAL_ORDER = ["1", "2", "4"]
UHF_GROUP = "1"
POWER_FREQ_HZ = 50.0
ETH_HEADER_BYTES = 14
PROTO_HEADER_BYTES = 2
CHANNEL_HEADER_BYTES = 6
EVENT_HEADER_BYTES = 10
PEAK_OFFSET_IN_PACKET = ETH_HEADER_BYTES + EVENT_HEADER_BYTES + 8
PHASE_OFFSET_IN_PACKET = PEAK_OFFSET_IN_PACKET + 2
WAVE_SAMPLE_START = ETH_HEADER_BYTES + PROTO_HEADER_BYTES + CHANNEL_HEADER_BYTES + 2 + 8
WAVE_CRC_BYTES = 4
VOLTAGE_CHANNEL_TYPE = 8

CM = 1 / 2.54
WIDE_FIGSIZE = (18 * CM, 11 * CM)
SMALL_FIGSIZE = (9 * CM, 5.5 * CM)

FILE_RE = re.compile(
    r"^(?P<kind>chara|wave|up_down)-(?P<group>\d+)-(?P<channel>\d+)-"
    r"(?P<start>\d{8}-\d{9})-(?P<end>\d{8}-\d{9})\.cap\.gz$"
)


@dataclass(frozen=True)
class CapFile:
    path: Path
    kind: str
    group: str
    channel: str
    feature: str
    signal_key: str
    signal_label: str
    start_text: str
    end_text: str


def configure_matplotlib() -> None:
    candidates = [
        "Microsoft YaHei",
        "SimHei",
        "Noto Sans CJK SC",
        "Arial Unicode MS",
        "DejaVu Sans",
    ]
    mpl.rcParams["font.sans-serif"] = candidates
    mpl.rcParams["axes.unicode_minus"] = False
    mpl.rcParams["pdf.fonttype"] = 42
    mpl.rcParams["ps.fonttype"] = 42
    mpl.rcParams["figure.dpi"] = 160


def parse_cap_files(input_dir: Path) -> list[CapFile]:
    files: list[CapFile] = []
    for path in sorted(input_dir.glob("*.cap.gz")):
        match = FILE_RE.match(path.name)
        if not match:
            continue
        group = match.group("group")
        channel = match.group("channel")
        files.append(
            CapFile(
                path=path,
                kind=match.group("kind"),
                group=group,
                channel=channel,
                feature=f"{group}-{channel}",
                signal_key=SIGNAL_TYPES.get(group, {"key": f"type_{group}"})["key"],
                signal_label=SIGNAL_TYPES.get(group, {"zh": f"类型{group}"})["zh"],
                start_text=match.group("start"),
                end_text=match.group("end"),
            )
        )
    return files


def iter_pcap_records(path: Path) -> Iterable[tuple[float, bytes]]:
    with gzip.open(path, "rb") as handle:
        global_header = handle.read(24)
        if len(global_header) != 24:
            return
        if global_header[:4] == b"\xd4\xc3\xb2\xa1":
            endian = "<"
        elif global_header[:4] == b"\xa1\xb2\xc3\xd4":
            endian = ">"
        else:
            raise ValueError(f"Not a supported PCAP file: {path}")

        while True:
            packet_header = handle.read(16)
            if len(packet_header) < 16:
                break
            ts_sec, ts_usec, incl_len, _orig_len = struct.unpack(endian + "IIII", packet_header)
            payload = handle.read(incl_len)
            if len(payload) != incl_len:
                break
            yield ts_sec + ts_usec / 1_000_000.0, payload


def payload_i16(payload: bytes, offset: int) -> int:
    if len(payload) < offset + 2:
        return 0
    return struct.unpack_from("<h", payload, offset)[0]


def payload_i32(payload: bytes, offset: int) -> int:
    if len(payload) < offset + 4:
        return 0
    return struct.unpack_from("<i", payload, offset)[0]


def phase_from_time(ts: float) -> float:
    period = 1.0 / POWER_FREQ_HZ
    return ((ts % period) / period) * 360.0


def phase_from_payload(payload: bytes) -> float:
    # Protocol A.5.5: phase is uint16 amplified 100 times, range 0-36000.
    # iter_pcap_records returns the whole Ethernet packet, so this is packet
    # byte 34 for one-channel 0xf010/0xf014 feature packets.
    if len(payload) < PHASE_OFFSET_IN_PACKET + 2:
        return 0.0
    phase_raw = struct.unpack_from("<H", payload, PHASE_OFFSET_IN_PACKET)[0]
    return phase_raw / 100.0


def channel_type_from_packet(packet: bytes) -> int | None:
    if len(packet) < ETH_HEADER_BYTES + PROTO_HEADER_BYTES + 3:
        return None
    return packet[ETH_HEADER_BYTES + 4]


def channel_unit_from_packet(packet: bytes) -> int | None:
    if len(packet) < ETH_HEADER_BYTES + PROTO_HEADER_BYTES + 4:
        return None
    return packet[ETH_HEADER_BYTES + 5]


def channel_id_from_packet(packet: bytes) -> int | None:
    if len(packet) < ETH_HEADER_BYTES + PROTO_HEADER_BYTES + 1:
        return None
    return packet[ETH_HEADER_BYTES + 2]


def ethertype_from_packet(packet: bytes) -> int | None:
    if len(packet) < ETH_HEADER_BYTES:
        return None
    return struct.unpack_from(">H", packet, 12)[0]


def parse_f010_events(packet: bytes) -> list[dict[str, object]]:
    proto = packet[ETH_HEADER_BYTES:]
    if len(proto) < 10:
        return []
    channel_count = proto[1]
    offset = 2
    rows: list[dict[str, object]] = []
    for _ in range(channel_count):
        if offset + CHANNEL_HEADER_BYTES + 10 > len(proto):
            break
        channel_id = proto[offset]
        frame_counter_raw = proto[offset + 1]
        channel_type = proto[offset + 2]
        unit = proto[offset + 3]
        noise_raw = struct.unpack_from("<h", proto, offset + 4)[0]
        offset += CHANNEL_HEADER_BYTES
        discharge_count = struct.unpack_from("<H", proto, offset)[0]
        offset += 2
        event_sec, event_nsec = struct.unpack_from("<II", proto, offset)
        offset += 8
        for event_index in range(discharge_count):
            if offset + 4 > len(proto):
                break
            peak_raw, phase_raw = struct.unpack_from("<hH", proto, offset)
            offset += 4
            rows.append(
                {
                    "channel_id": channel_id,
                    "frame_counter_raw": frame_counter_raw,
                    "channel_type": channel_type,
                    "unit": unit,
                    "noise_raw": noise_raw,
                    "discharge_count": discharge_count,
                    "event_index": event_index + 1,
                    "event_sec": event_sec,
                    "event_nsec": event_nsec,
                    "peak_raw": peak_raw,
                    "phase_raw": phase_raw,
                    "phase_deg": phase_raw / 100.0,
                }
            )
    return rows


def parse_first_f014_event(packet: bytes) -> dict[str, object] | None:
    proto = packet[ETH_HEADER_BYTES:]
    if len(proto) < 30:
        return None
    offset = 2
    if offset + CHANNEL_HEADER_BYTES + 2 + 20 > len(proto):
        return None
    channel_id = proto[offset]
    frame_counter_raw = proto[offset + 1]
    channel_type = proto[offset + 2]
    unit = proto[offset + 3]
    noise_raw = struct.unpack_from("<h", proto, offset + 4)[0]
    offset += CHANNEL_HEADER_BYTES
    discharge_count = struct.unpack_from("<H", proto, offset)[0]
    offset += 2
    event_sec, event_nsec = struct.unpack_from("<II", proto, offset)
    peak_raw, phase_raw = struct.unpack_from("<hH", proto, offset + 8)
    rise_ps, decay_ps = struct.unpack_from("<II", proto, offset + 12)
    return {
        "channel_id": channel_id,
        "frame_counter_raw": frame_counter_raw,
        "channel_type": channel_type,
        "unit": unit,
        "noise_raw": noise_raw,
        "discharge_count": discharge_count,
        "event_index": 1,
        "event_sec": event_sec,
        "event_nsec": event_nsec,
        "peak_raw": peak_raw,
        "phase_raw": phase_raw,
        "phase_deg": phase_raw / 100.0,
        "rise_ps": rise_ps,
        "decay_ps": decay_ps,
    }


def read_chara(files: list[CapFile]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for cap in files:
        if cap.kind != "chara" or cap.group not in SIGNAL_TYPES:
            continue
        for ts, payload in iter_pcap_records(cap.path):
            if ethertype_from_packet(payload) == 0xF010:
                events = parse_f010_events(payload)
            elif ethertype_from_packet(payload) == 0xF014:
                first = parse_first_f014_event(payload)
                events = [first] if first is not None else []
            else:
                events = []
            for event in events:
                packet_channel_type = int(event["channel_type"])
                unit = int(event["unit"])
                if str(packet_channel_type) in SIGNAL_TYPES:
                    signal_group = str(packet_channel_type)
                    signal_key = SIGNAL_TYPES[signal_group]["key"]
                    signal_label = SIGNAL_TYPES[signal_group]["zh"]
                else:
                    signal_group = cap.group
                    signal_key = cap.signal_key
                    signal_label = cap.signal_label
                phase_deg = float(event["phase_deg"])
                rows.append(
                    {
                        "time_s": ts,
                        "datetime": datetime.fromtimestamp(ts),
                        "signal_group": signal_group,
                        "signal_key": signal_key,
                        "signal_label": signal_label,
                        "feature": cap.feature,
                        "channel": event["channel_id"],
                        "channel_type": packet_channel_type,
                        "unit": unit,
                        "unit_name": "mV" if unit == 2 else ("dBm" if unit == 1 else f"UNIT_{unit}"),
                        "noise_raw": event["noise_raw"],
                        "discharge_count": event["discharge_count"],
                        "event_index": event["event_index"],
                        "event_sec": event["event_sec"],
                        "event_nsec": event["event_nsec"],
                        "amplitude_raw": event["peak_raw"],
                        "amplitude_abs_raw": abs(int(event["peak_raw"])),
                        "phase_raw": event["phase_raw"],
                        "phase_deg": phase_deg,
                        "phase_time_ms": phase_deg / 360.0 * (1000.0 / POWER_FREQ_HZ),
                        "payload_len": len(payload),
                        "source_file": cap.path.name,
                    }
                )
    df = pd.DataFrame(rows)
    if not df.empty:
        first = df["time_s"].min()
        df["elapsed_s"] = df["time_s"] - first
    return df


def read_up_down_voltage_proxy(files: list[CapFile]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for cap in files:
        if cap.kind not in {"chara", "up_down"}:
            continue
        for ts, payload in iter_pcap_records(cap.path):
            if ethertype_from_packet(payload) == 0xF010:
                events = parse_f010_events(payload)
            elif ethertype_from_packet(payload) == 0xF014:
                first = parse_first_f014_event(payload)
                events = [first] if first is not None else []
            else:
                events = []
            for event in events:
                if int(event["channel_type"]) != VOLTAGE_CHANNEL_TYPE:
                    continue
                unit = int(event["unit"])
                rows.append(
                    {
                        "time_s": ts,
                        "datetime": datetime.fromtimestamp(ts),
                        "signal_group": str(VOLTAGE_CHANNEL_TYPE),
                        "signal_label": "阀侧末屏电压",
                        "feature": cap.feature,
                        "channel": event["channel_id"],
                        "unit": unit,
                        "unit_name": "mV" if unit == 2 else ("dBm" if unit == 1 else f"UNIT_{unit}"),
                        "applied_voltage_raw": event["peak_raw"],
                        "source_file": cap.path.name,
                    }
                )
            if events:
                continue
    df = pd.DataFrame(rows)
    if not df.empty:
        df["elapsed_s"] = df["time_s"] - df["time_s"].min()
    return df


def read_wave_packets(files: list[CapFile], group: str) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for cap in files:
        if cap.kind != "wave" or cap.group != group:
            continue
        for ts, payload in iter_pcap_records(cap.path):
            if len(payload) <= WAVE_SAMPLE_START + WAVE_CRC_BYTES:
                continue
            raw = payload[WAVE_SAMPLE_START : len(payload) - WAVE_CRC_BYTES]
            if len(raw) % 2:
                raw = raw[:-1]
            samples = np.frombuffer(raw, dtype="<i2").astype(np.float64)
            if samples.size == 0:
                continue
            event_sec = payload_i32(payload, 24)
            event_frac = struct.unpack_from("<I", payload, 28)[0] if len(payload) >= 32 else 0
            rows.append(
                {
                    "time_s": ts,
                    "datetime": datetime.fromtimestamp(ts),
                    "signal_group": cap.group,
                    "signal_key": cap.signal_key,
                    "signal_label": cap.signal_label,
                    "feature": cap.feature,
                    "channel": cap.channel,
                    "sample_count": int(samples.size),
                    "mean_raw": float(samples.mean()),
                    "rms_raw": float(np.sqrt(np.mean(samples**2))),
                    "peak_abs_raw": float(np.max(np.abs(samples))),
                    "event_sec": event_sec,
                    "event_frac": event_frac,
                    "samples": samples,
                }
            )
    df = pd.DataFrame(rows)
    if not df.empty:
        df["elapsed_s"] = df["time_s"] - df["time_s"].min()
    return df


def iter_wave_packets(files: list[CapFile], group: str) -> Iterable[dict[str, object]]:
    for cap in files:
        if cap.kind != "wave" or cap.group != group:
            continue
        for ts, payload in iter_pcap_records(cap.path):
            if len(payload) <= WAVE_SAMPLE_START + WAVE_CRC_BYTES:
                continue
            raw = payload[WAVE_SAMPLE_START : len(payload) - WAVE_CRC_BYTES]
            if len(raw) % 2:
                raw = raw[:-1]
            samples = np.frombuffer(raw, dtype="<i2").astype(np.float64)
            if samples.size == 0:
                continue
            event_sec = payload_i32(payload, 24)
            event_frac = struct.unpack_from("<I", payload, 28)[0] if len(payload) >= 32 else 0
            yield {
                "time_s": ts,
                "datetime": datetime.fromtimestamp(ts),
                "signal_group": cap.group,
                "signal_key": cap.signal_key,
                "signal_label": cap.signal_label,
                "feature": cap.feature,
                "channel": cap.channel,
                "sample_count": int(samples.size),
                "mean_raw": float(samples.mean()),
                "rms_raw": float(np.sqrt(np.mean(samples**2))),
                "peak_abs_raw": float(np.max(np.abs(samples))),
                "event_sec": event_sec,
                "event_frac": event_frac,
                "source_file": cap.path.name,
                "samples": samples,
            }


def estimate_sample_rate(wave_df: pd.DataFrame) -> float:
    if wave_df.empty:
        return 1.0
    # Adjacent packets with the same embedded event timestamp are fragments of one waveform.
    sample_rates: list[float] = []
    cols = ["event_sec", "event_frac"]
    for _key, group in wave_df.sort_values("time_s").groupby(cols, sort=False):
        if len(group) < 2:
            continue
        times = group["time_s"].to_numpy()
        counts = group["sample_count"].to_numpy()
        dt = np.diff(times)
        valid = dt > 0
        if np.any(valid):
            sample_rates.extend((counts[:-1][valid] / dt[valid]).tolist())
        if len(sample_rates) >= 200:
            break
    if sample_rates:
        return float(np.median(sample_rates))
    if "sample_count" in wave_df:
        return float(np.median(wave_df["sample_count"]) * 1_000_000.0)
    return 1.0


def voltage_proxy(files: list[CapFile]) -> pd.DataFrame:
    up_down = read_up_down_voltage_proxy(files)
    if not up_down.empty:
        out = (
            up_down.groupby(pd.Grouper(key="datetime", freq="1s"))["applied_voltage_raw"]
            .median()
            .dropna()
            .reset_index()
        )
        out = out.rename(columns={"applied_voltage_raw": "applied_voltage_raw"})
        out["time_s"] = out["datetime"].map(lambda x: x.timestamp())
        out["elapsed_s"] = out["time_s"] - out["time_s"].min()
        out["source"] = "channel_type 8 阀侧末屏电压"
        return out
    return pd.DataFrame()


def make_time_series(chara_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if chara_df.empty:
        return pd.DataFrame(), pd.DataFrame()
    indexed = chara_df.set_index("datetime")
    amp = (
        indexed.groupby(["signal_group", "signal_key", "signal_label"])
        .resample("1s")["amplitude_abs_raw"]
        .median()
        .rename("amplitude_abs_median_raw")
        .reset_index()
    )
    count = (
        indexed.groupby(["signal_group", "signal_key", "signal_label"])
        .resample("1s")["amplitude_raw"]
        .count()
        .rename("repetition_rate_per_s")
        .reset_index()
    )
    first_dt = min(amp["datetime"].min(), count["datetime"].min())
    for df in (amp, count):
        df["elapsed_s"] = (df["datetime"] - first_dt).dt.total_seconds()
        df["time_s"] = first_dt.timestamp() + df["elapsed_s"]
    return amp.dropna(), count.dropna()


def aggregate_voltage_1s(voltage_df: pd.DataFrame) -> pd.DataFrame:
    if voltage_df.empty:
        return pd.DataFrame(columns=["datetime", "elapsed_s", "time_s", "applied_voltage_raw", "source"])
    agg = (
        voltage_df.set_index("datetime")
        .resample("1s")["applied_voltage_raw"]
        .median()
        .dropna()
        .reset_index()
    )
    first_dt = agg["datetime"].min()
    agg["elapsed_s"] = (agg["datetime"] - first_dt).dt.total_seconds()
    agg["time_s"] = voltage_df["time_s"].min() + agg["elapsed_s"]
    agg["source"] = str(voltage_df["source"].iloc[0]) if "source" in voltage_df else "voltage proxy"
    return agg


def make_origin_time_table(
    signal_df: pd.DataFrame,
    value_column: str,
    value_suffix: str,
    voltage_df: pd.DataFrame,
) -> pd.DataFrame:
    if signal_df.empty:
        return pd.DataFrame()
    first_dt = signal_df["datetime"].min()
    last_dt = signal_df["datetime"].max()
    voltage_1s = aggregate_voltage_1s(voltage_df)
    if not voltage_1s.empty:
        first_dt = min(first_dt, voltage_1s["datetime"].min())
        last_dt = max(last_dt, voltage_1s["datetime"].max())
    timeline = pd.DataFrame({"datetime": pd.date_range(first_dt, last_dt, freq="1s")})
    timeline["elapsed_s"] = (timeline["datetime"] - timeline["datetime"].min()).dt.total_seconds()
    out = timeline
    for signal_group in SIGNAL_ORDER:
        label = SIGNAL_TYPES[signal_group]["zh"]
        part = signal_df[signal_df["signal_group"] == signal_group][["datetime", value_column]].copy()
        part = part.rename(columns={value_column: f"{label}_{value_suffix}"})
        out = out.merge(part, on="datetime", how="left")
    if not voltage_1s.empty:
        out = out.merge(
            voltage_1s[["datetime", "applied_voltage_raw"]].rename(columns={"applied_voltage_raw": "外施电压_raw"}),
            on="datetime",
            how="left",
        )
    else:
        out["外施电压_raw"] = np.nan
    return out


def make_origin_main_frequency_table(peak_df: pd.DataFrame) -> pd.DataFrame:
    if peak_df.empty:
        return peak_df
    rows = []
    for (dt, elapsed), part in peak_df.groupby(["datetime", "elapsed_s"], sort=True):
        row = {"datetime": dt, "elapsed_s": elapsed}
        for _, item in part.sort_values("rank").iterrows():
            rank = int(item["rank"])
            row[f"主频{rank}_MHz"] = item["frequency_mhz"]
            row[f"主频{rank}_幅值"] = item["magnitude"]
        rows.append(row)
    return pd.DataFrame(rows)


def spectrum_evolution_features(spec_df: pd.DataFrame, minute: pd.Timestamp) -> dict[str, object]:
    freq_hz = spec_df["frequency_hz"].to_numpy(dtype=float)
    freq_mhz = freq_hz / 1e6
    mag = spec_df["magnitude"].to_numpy(dtype=float)
    power = mag**2
    total_power = float(power.sum())
    if total_power <= 0:
        centroid = 0.0
        bandwidth = 0.0
    else:
        centroid = float((freq_mhz * power).sum() / total_power)
        bandwidth = float(np.sqrt((((freq_mhz - centroid) ** 2) * power).sum() / total_power))

    def band_power(lo: float, hi: float) -> float:
        mask = (freq_mhz >= lo) & (freq_mhz < hi)
        return float(power[mask].sum())

    bands = {
        "band_0_3_mhz_power": band_power(0, 3),
        "band_3_6_mhz_power": band_power(3, 6),
        "band_6_10_mhz_power": band_power(6, 10),
        "band_10_plus_mhz_power": float(power[freq_mhz >= 10].sum()),
    }
    row: dict[str, object] = {
        "minute": minute.strftime("%Y-%m-%d %H:%M:%S"),
        "spectral_centroid_mhz": centroid,
        "spectral_bandwidth_mhz": bandwidth,
        "total_power": total_power,
        "max_magnitude": float(mag.max()) if mag.size else 0.0,
    }
    for key, value in bands.items():
        row[key] = value
        row[key.replace("_power", "_ratio")] = value / total_power if total_power > 0 else 0.0
    return row


def plot_uhf_time_frequency_evolution(
    spectra_long: pd.DataFrame,
    peak_df: pd.DataFrame,
    feature_df: pd.DataFrame,
    plot_dir: Path,
    fig_dir: Path,
) -> None:
    if spectra_long.empty:
        return

    out_plot_dir = plot_dir / "uhf_time_frequency"
    out_fig_dir = fig_dir / "uhf_time_frequency"
    out_plot_dir.mkdir(parents=True, exist_ok=True)
    out_fig_dir.mkdir(parents=True, exist_ok=True)

    safe_to_csv(spectra_long, out_plot_dir / "origin_uhf_time_frequency_spectra_long.csv")
    if not feature_df.empty:
        safe_to_csv(feature_df, out_plot_dir / "origin_uhf_spectral_features_by_minute.csv")

    spectra = spectra_long.copy()
    spectra["minute"] = pd.to_datetime(spectra["minute"])
    minutes = sorted(spectra["minute"].unique())
    freq_values = np.sort(spectra["frequency_mhz"].unique())
    matrix = (
        spectra.pivot_table(index="frequency_mhz", columns="minute", values="magnitude", aggfunc="mean")
        .reindex(index=freq_values, columns=minutes)
        .fillna(0.0)
    )
    matrix_log = np.log10(matrix.to_numpy(dtype=float) + 1.0)
    pd.DataFrame(matrix.to_numpy(), index=matrix.index, columns=[pd.Timestamp(m).strftime("%Y-%m-%d %H:%M:%S") for m in minutes]).to_csv(
        out_plot_dir / "origin_uhf_time_frequency_matrix.csv", encoding="utf-8-sig"
    )

    x = np.arange(len(minutes) + 1)
    if len(freq_values) > 1:
        step = np.median(np.diff(freq_values))
    else:
        step = 1.0
    y = np.r_[freq_values, freq_values[-1] + step] if len(freq_values) else np.array([0, 1])

    fig, ax = plt.subplots(figsize=WIDE_FIGSIZE)
    mesh = ax.pcolormesh(x, y, matrix_log, shading="auto", cmap="turbo")
    tick_idx = np.linspace(0, max(len(minutes) - 1, 0), min(len(minutes), 8), dtype=int)
    ax.set_xticks(tick_idx + 0.5)
    ax.set_xticklabels([pd.Timestamp(minutes[i]).strftime("%H:%M") for i in tick_idx], rotation=35, ha="right")
    ax.set_xlabel("Time")
    ax.set_ylabel("Frequency (MHz)")
    ax.set_title("UHF time-frequency map")
    cbar = fig.colorbar(mesh, ax=ax)
    cbar.set_label("log10(magnitude + 1)")
    save_figure(fig, out_fig_dir / "uhf_time_frequency_map")

    fig, ax = plt.subplots(figsize=WIDE_FIGSIZE)
    scale = spectra["magnitude"].quantile(0.98)
    scale = float(scale) if scale and scale > 0 else 1.0
    for idx, minute in enumerate(minutes):
        part = spectra[spectra["minute"] == minute].sort_values("frequency_mhz")
        ax.plot(part["frequency_mhz"], part["magnitude"] / scale + idx, lw=0.8)
    ax.set_xlabel("Frequency (MHz)")
    ax.set_ylabel("Minute index + normalized magnitude")
    ax.set_title("UHF spectrum waterfall")
    ax.grid(True, alpha=0.25)
    save_figure(fig, out_fig_dir / "uhf_spectrum_waterfall")

    if not peak_df.empty:
        safe_to_csv(peak_df, out_plot_dir / "origin_uhf_main_frequency_by_minute_long.csv")
        fig, ax = plt.subplots(figsize=WIDE_FIGSIZE)
        peak_plot = peak_df.copy()
        peak_plot["minute"] = pd.to_datetime(peak_plot["minute"])
        for rank, part in peak_plot.groupby("rank"):
            ax.plot(part["minute"], part["frequency_mhz"], "o-", ms=3, lw=1.0, label=f"Peak {rank}")
        ax.set_xlabel("Time")
        ax.set_ylabel("Main frequency (MHz)")
        ax.set_title("UHF main frequency evolution")
        ax.grid(True, alpha=0.25)
        ax.legend(fontsize=8)
        fig.autofmt_xdate()
        save_figure(fig, out_fig_dir / "uhf_main_frequency_evolution")

    if not feature_df.empty:
        features = feature_df.copy()
        features["minute"] = pd.to_datetime(features["minute"])
        fig, ax = plt.subplots(figsize=WIDE_FIGSIZE)
        ax.plot(features["minute"], features["spectral_centroid_mhz"], "o-", ms=3, label="Centroid")
        ax.plot(features["minute"], features["spectral_bandwidth_mhz"], "s-", ms=3, label="Bandwidth")
        ax.set_xlabel("Time")
        ax.set_ylabel("Frequency (MHz)")
        ax.set_title("UHF spectral centroid and bandwidth")
        ax.grid(True, alpha=0.25)
        ax.legend(fontsize=8)
        fig.autofmt_xdate()
        save_figure(fig, out_fig_dir / "uhf_centroid_bandwidth_evolution")

        ratio_cols = [
            "band_0_3_mhz_ratio",
            "band_3_6_mhz_ratio",
            "band_6_10_mhz_ratio",
            "band_10_plus_mhz_ratio",
        ]
        fig, ax = plt.subplots(figsize=WIDE_FIGSIZE)
        labels = ["0-3 MHz", "3-6 MHz", "6-10 MHz", ">=10 MHz"]
        for col, label in zip(ratio_cols, labels):
            ax.plot(features["minute"], features[col], "o-", ms=3, lw=1.0, label=label)
        ax.set_xlabel("Time")
        ax.set_ylabel("Power ratio")
        ax.set_title("UHF band-energy ratio evolution")
        ax.set_ylim(bottom=0)
        ax.grid(True, alpha=0.25)
        ax.legend(fontsize=8)
        fig.autofmt_xdate()
        save_figure(fig, out_fig_dir / "uhf_band_energy_ratio_evolution")


def choose_typical_windows(chara_df: pd.DataFrame) -> pd.DataFrame:
    if chara_df.empty:
        return pd.DataFrame()
    selected: list[pd.DataFrame] = []
    indexed = chara_df.set_index("datetime")
    for signal_group in SIGNAL_ORDER:
        part = indexed[indexed["signal_group"] == signal_group]
        if part.empty:
            continue
        counts = part.resample("10s")["amplitude_raw"].count()
        counts = counts[counts > 0]
        if counts.empty:
            continue
        start = counts.idxmax()
        stop = start + pd.Timedelta(seconds=10)
        window = part[(part.index >= start) & (part.index < stop)].copy()
        if not window.empty:
            window["window_start"] = start
            selected.append(window.reset_index())
    return pd.concat(selected, ignore_index=True) if selected else pd.DataFrame()


def prpd_matrix(events: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    max_amp = float(events["amplitude_abs_raw"].max())
    if max_amp <= 0:
        max_amp = 1.0
    phase_edges = np.arange(0, 365, 5, dtype=float)
    amp_edges = np.linspace(-max_amp, max_amp, 401)
    hist, phase_edges, amp_edges = np.histogram2d(
        events["phase_deg"].to_numpy(),
        events["amplitude_raw"].to_numpy(),
        bins=[phase_edges, amp_edges],
    )
    duration = max(events["time_s"].max() - events["time_s"].min(), 1.0)
    return hist.T / duration, phase_edges, amp_edges


def prpd_event_table(events: pd.DataFrame, matrix: np.ndarray, phase_edges: np.ndarray, amp_edges: np.ndarray) -> pd.DataFrame:
    """Return one row per discharge event, colored by its bin repetition rate."""
    if events.empty:
        return pd.DataFrame()

    table = events[
        [
            "datetime",
            "time_s",
            "elapsed_s",
            "signal_label",
            "feature",
            "channel",
            "event_index",
            "phase_deg",
            "phase_time_ms",
            "amplitude_raw",
            "amplitude_abs_raw",
        ]
    ].copy()

    phase_values = table["phase_deg"].to_numpy(dtype=float)
    amp_values = table["amplitude_raw"].to_numpy(dtype=float)
    phase_idx = np.searchsorted(phase_edges, phase_values, side="right") - 1
    amp_idx = np.searchsorted(amp_edges, amp_values, side="right") - 1
    phase_idx = np.clip(phase_idx, 0, len(phase_edges) - 2)
    amp_idx = np.clip(amp_idx, 0, len(amp_edges) - 2)

    table["phase_bin_left_deg"] = phase_edges[phase_idx]
    table["phase_bin_right_deg"] = phase_edges[phase_idx + 1]
    table["amplitude_bin_left_raw"] = amp_edges[amp_idx]
    table["amplitude_bin_right_raw"] = amp_edges[amp_idx + 1]
    table["repetition_rate_per_s"] = matrix[amp_idx, phase_idx]
    return table


def representative_waveforms(wave_df: pd.DataFrame, max_items: int = 5) -> pd.DataFrame:
    if wave_df.empty:
        return wave_df
    ranked = wave_df.sort_values("peak_abs_raw", ascending=False)
    # Keep separated timestamps so plots are not duplicates from one burst.
    picked = []
    last_times: list[float] = []
    for _, row in ranked.iterrows():
        ts = float(row["time_s"])
        if all(abs(ts - old) >= 0.5 for old in last_times):
            picked.append(row)
            last_times.append(ts)
        if len(picked) >= max_items:
            break
    return pd.DataFrame(picked)


def spectrum_from_samples(samples: np.ndarray, sample_rate_hz: float) -> pd.DataFrame:
    centered = samples.astype(float) - float(np.mean(samples))
    if centered.size < 8:
        return pd.DataFrame(columns=["frequency_hz", "magnitude"])
    window = np.hanning(centered.size)
    spec = np.fft.rfft(centered * window)
    freq = np.fft.rfftfreq(centered.size, d=1.0 / sample_rate_hz)
    mag = np.abs(spec)
    return pd.DataFrame({"frequency_hz": freq, "magnitude": mag})


def extract_main_frequencies(spec_df: pd.DataFrame, max_peaks: int = 5) -> list[tuple[float, float]]:
    if spec_df.empty:
        return []
    spec = spec_df[spec_df["frequency_hz"] > 0].copy()
    if spec.empty:
        return []
    mag = spec["magnitude"].to_numpy()
    if mag.max() <= 0:
        return []
    peaks, props = find_peaks(mag, prominence=mag.max() * 0.08, distance=3)
    if len(peaks) == 0:
        top = int(np.argmax(mag))
        return [(float(spec.iloc[top]["frequency_hz"]), float(spec.iloc[top]["magnitude"]))]
    prominences = props.get("prominences", np.zeros(len(peaks)))
    order = np.argsort(prominences)[::-1][:max_peaks]
    out = []
    for idx in order:
        row = spec.iloc[int(peaks[idx])]
        out.append((float(row["frequency_hz"]), float(row["magnitude"])))
    return sorted(out)


def save_figure(fig: plt.Figure, out_base: Path) -> None:
    out_base.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_base.with_suffix(".png"), bbox_inches="tight")
    fig.savefig(out_base.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)


def safe_to_csv(df: pd.DataFrame, path: Path) -> Path:
    try:
        df.to_csv(path, index=False, encoding="utf-8-sig")
        return path
    except PermissionError:
        fallback = path.with_name(f"{path.stem}_updated{path.suffix}")
        df.to_csv(fallback, index=False, encoding="utf-8-sig")
        return fallback


def plot_amplitude(amp_df: pd.DataFrame, voltage_df: pd.DataFrame, fig_dir: Path) -> None:
    fig, ax = plt.subplots(figsize=WIDE_FIGSIZE)
    for signal_group in SIGNAL_ORDER:
        part = amp_df[amp_df["signal_group"] == signal_group]
        if not part.empty:
            label = SIGNAL_TYPES[signal_group]["zh"]
            ax.plot(part["datetime"], part["amplitude_abs_median_raw"], lw=1.2, label=label)
    ax.set_xlabel("时间")
    ax.set_ylabel("幅值中值 (raw)")
    ax.grid(True, alpha=0.25)
    ax2 = ax.twinx()
    voltage_plot = aggregate_voltage_1s(voltage_df)
    if not voltage_plot.empty:
        ax2.plot(voltage_plot["datetime"], voltage_plot["applied_voltage_raw"], color="black", lw=1.0, alpha=0.65, label="外施电压")
    ax2.set_ylabel("外施电压 (raw)")
    lines, labels = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines + lines2, labels + labels2, loc="upper left", fontsize=8)
    fig.autofmt_xdate()
    save_figure(fig, fig_dir / "01_feature_amplitude_and_voltage")


def plot_repetition(rate_df: pd.DataFrame, voltage_df: pd.DataFrame, fig_dir: Path) -> None:
    fig, ax = plt.subplots(figsize=WIDE_FIGSIZE)
    for signal_group in SIGNAL_ORDER:
        part = rate_df[rate_df["signal_group"] == signal_group]
        if not part.empty:
            label = SIGNAL_TYPES[signal_group]["zh"]
            ax.plot(part["datetime"], part["repetition_rate_per_s"], lw=1.2, label=label)
    ax.set_xlabel("时间")
    ax.set_ylabel("重复率 (1/s)")
    ax.grid(True, alpha=0.25)
    ax2 = ax.twinx()
    voltage_plot = aggregate_voltage_1s(voltage_df)
    if not voltage_plot.empty:
        ax2.plot(voltage_plot["datetime"], voltage_plot["applied_voltage_raw"], color="black", lw=1.0, alpha=0.65, label="外施电压")
    ax2.set_ylabel("外施电压 (raw)")
    lines, labels = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines + lines2, labels + labels2, loc="upper left", fontsize=8)
    fig.autofmt_xdate()
    save_figure(fig, fig_dir / "02_feature_repetition_and_voltage")


def plot_prpd(chara_df: pd.DataFrame, plot_dir: Path, fig_dir: Path) -> None:
    typical = choose_typical_windows(chara_df)
    if typical.empty:
        return
    safe_to_csv(typical, plot_dir / "typical_prpd_events.csv")
    for signal_group in SIGNAL_ORDER:
        events = typical[typical["signal_group"] == signal_group]
        if events.empty:
            continue
        matrix, phase_edges, amp_edges = prpd_matrix(events)
        signal_label = SIGNAL_TYPES[signal_group]["zh"]
        signal_key = SIGNAL_TYPES[signal_group]["key"]
        np.savez_compressed(
            plot_dir / f"prpd_{signal_key}.npz",
            repetition_rate=matrix,
            phase_edges_deg=phase_edges,
            amplitude_edges_raw=amp_edges,
        )
        table = prpd_event_table(events, matrix, phase_edges, amp_edges)
        safe_to_csv(table, plot_dir / f"origin_03_prpd_{signal_key}.csv")
        fig, ax = plt.subplots(figsize=SMALL_FIGSIZE)
        ax.set_facecolor("#eeeeea")
        scatter = ax.scatter(
            table["phase_deg"],
            table["amplitude_raw"],
            c=table["repetition_rate_per_s"],
            s=4.0,
            marker=".",
            cmap="jet",
            linewidths=0,
            alpha=0.65,
        )
        max_amp = max(abs(float(amp_edges[0])), abs(float(amp_edges[-1])))
        x = np.linspace(0, 360, 720)
        ax.plot(x, np.sin(np.deg2rad(x)) * max_amp * 0.88, color="#23aa35", lw=1.0)
        ax.set_xlim(0, 360)
        ax.set_ylim(-max_amp, max_amp)
        ax.set_xlabel("相位 (deg)")
        ax.set_ylabel("幅值 (raw)")
        ax.set_title(signal_label, fontsize=9)
        ax.grid(True, which="major", color="#9c9c9c", alpha=0.55, lw=0.6)
        ax.minorticks_on()
        ax.grid(True, which="minor", color="#b8b8b8", alpha=0.35, lw=0.4)
        top = ax.secondary_xaxis(
            "top",
            functions=(lambda deg: deg / 360.0 * (1000.0 / POWER_FREQ_HZ), lambda ms: ms / (1000.0 / POWER_FREQ_HZ) * 360.0),
        )
        top.set_xlabel("工频周期时间 (ms)")
        top.set_xticks([2, 6, 10, 14, 18])
        cbar = fig.colorbar(scatter, ax=ax)
        cbar.set_label("重复率 (1/s)")
        save_figure(fig, fig_dir / f"03_prpd_{signal_key}")


def render_prpd_figure(table: pd.DataFrame, amp_edges: np.ndarray, signal_label: str, title_suffix: str, out_base: Path) -> None:
    fig, ax = plt.subplots(figsize=SMALL_FIGSIZE)
    ax.set_facecolor("#eeeeea")
    scatter = ax.scatter(
        table["phase_deg"],
        table["amplitude_raw"],
        c=table["repetition_rate_per_s"],
        s=4.0,
        marker=".",
        cmap="jet",
        linewidths=0,
        alpha=0.65,
    )
    max_amp = max(abs(float(amp_edges[0])), abs(float(amp_edges[-1])))
    x = np.linspace(0, 360, 720)
    ax.plot(x, np.sin(np.deg2rad(x)) * max_amp * 0.88, color="#23aa35", lw=1.0)
    ax.set_xlim(0, 360)
    ax.set_ylim(-max_amp, max_amp)
    ax.set_xlabel("相位 (deg)")
    ax.set_ylabel("幅值 (raw)")
    ax.set_title(f"{signal_label} {title_suffix}", fontsize=9)
    ax.grid(True, which="major", color="#9c9c9c", alpha=0.55, lw=0.6)
    ax.minorticks_on()
    ax.grid(True, which="minor", color="#b8b8b8", alpha=0.35, lw=0.4)
    top = ax.secondary_xaxis(
        "top",
        functions=(lambda deg: deg / 360.0 * (1000.0 / POWER_FREQ_HZ), lambda ms: ms / (1000.0 / POWER_FREQ_HZ) * 360.0),
    )
    top.set_xlabel("工频周期时间 (ms)")
    top.set_xticks([2, 6, 10, 14, 18])
    cbar = fig.colorbar(scatter, ax=ax)
    cbar.set_label("重复率 (1/s)")
    save_figure(fig, out_base)


def plot_prpd(chara_df: pd.DataFrame, plot_dir: Path, fig_dir: Path) -> None:
    if chara_df.empty:
        return

    prpd_plot_dir = plot_dir / "prpd_by_minute"
    prpd_fig_dir = fig_dir / "03_prpd_by_minute"
    index_rows: list[dict[str, object]] = []
    segment_index_rows: list[dict[str, object]] = []
    segment_start_hms = (9, 53, 20)
    segment_end_hms = (9, 53, 50)

    for signal_group in SIGNAL_ORDER:
        signal_events = chara_df[chara_df["signal_group"] == signal_group].copy()
        if signal_events.empty:
            continue

        signal_label = SIGNAL_TYPES[signal_group]["zh"]
        signal_key = SIGNAL_TYPES[signal_group]["key"]
        signal_plot_dir = prpd_plot_dir / signal_key
        signal_fig_dir = prpd_fig_dir / signal_key
        signal_plot_dir.mkdir(parents=True, exist_ok=True)
        signal_fig_dir.mkdir(parents=True, exist_ok=True)

        signal_events["minute"] = pd.to_datetime(signal_events["datetime"]).dt.floor("min")
        safe_to_csv(
            signal_events[
                [
                    "datetime",
                    "time_s",
                    "elapsed_s",
                    "signal_label",
                    "feature",
                    "channel",
                    "event_index",
                    "phase_deg",
                    "phase_time_ms",
                    "amplitude_raw",
                    "amplitude_abs_raw",
                    "minute",
                ]
            ],
            signal_plot_dir / f"all_prpd_events_{signal_key}.csv",
        )

        for minute, events in signal_events.groupby("minute", sort=True):
            if events.empty:
                continue
            matrix, phase_edges, amp_edges = prpd_matrix(events)
            table = prpd_event_table(events, matrix, phase_edges, amp_edges)
            minute_ts = pd.Timestamp(minute)
            minute_tag = minute_ts.strftime("%Y%m%d_%H%M")
            base_name = f"03_prpd_{signal_key}_{minute_tag}"

            safe_to_csv(table, signal_plot_dir / f"origin_{base_name}.csv")
            np.savez_compressed(
                signal_plot_dir / f"{base_name}.npz",
                repetition_rate=matrix,
                phase_edges_deg=phase_edges,
                amplitude_edges_raw=amp_edges,
            )
            render_prpd_figure(table, amp_edges, signal_label, minute_ts.strftime("%Y-%m-%d %H:%M"), signal_fig_dir / base_name)
            index_rows.append(
                {
                    "signal_key": signal_key,
                    "signal_label": signal_label,
                    "minute": minute_ts.strftime("%Y-%m-%d %H:%M:%S"),
                    "event_count": len(events),
                    "csv_file": str(signal_plot_dir / f"origin_{base_name}.csv"),
                    "png_file": str(signal_fig_dir / f"{base_name}.png"),
                    "pdf_file": str(signal_fig_dir / f"{base_name}.pdf"),
                }
            )

        event_dt = pd.to_datetime(signal_events["datetime"])
        tod_seconds = event_dt.dt.hour * 3600 + event_dt.dt.minute * 60 + event_dt.dt.second + event_dt.dt.microsecond / 1_000_000.0
        segment_start_seconds = segment_start_hms[0] * 3600 + segment_start_hms[1] * 60 + segment_start_hms[2]
        segment_end_seconds = segment_end_hms[0] * 3600 + segment_end_hms[1] * 60 + segment_end_hms[2]
        segment_events = signal_events[(tod_seconds >= segment_start_seconds) & (tod_seconds < segment_end_seconds)].copy()
        if not segment_events.empty:
            matrix, phase_edges, amp_edges = prpd_matrix(segment_events)
            table = prpd_event_table(segment_events, matrix, phase_edges, amp_edges)
            segment_date = pd.Timestamp(segment_events["datetime"].iloc[0]).strftime("%Y%m%d")
            segment_label = f"{segment_date}_095320_095350"
            base_name = f"03_prpd_{signal_key}_{segment_label}"
            csv_path = safe_to_csv(table, signal_plot_dir / f"origin_{base_name}.csv")
            np.savez_compressed(
                signal_plot_dir / f"{base_name}.npz",
                repetition_rate=matrix,
                phase_edges_deg=phase_edges,
                amplitude_edges_raw=amp_edges,
            )
            render_prpd_figure(table, amp_edges, signal_label, "2026-04-17 09:53:20 to 09:53:50", signal_fig_dir / base_name)
            segment_index_rows.append(
                {
                    "signal_key": signal_key,
                    "signal_label": signal_label,
                    "segment": "09:53:20-09:53:50",
                    "event_count": len(segment_events),
                    "csv_file": str(csv_path),
                    "png_file": str(signal_fig_dir / f"{base_name}.png"),
                    "pdf_file": str(signal_fig_dir / f"{base_name}.pdf"),
                }
            )

    if index_rows:
        safe_to_csv(pd.DataFrame(index_rows), plot_dir / "origin_03_prpd_by_minute_index.csv")
    if segment_index_rows:
        safe_to_csv(pd.DataFrame(segment_index_rows), plot_dir / "origin_03_prpd_segment_095320_095350_index.csv")


def plot_fft_and_main_freqs(files: list[CapFile], plot_dir: Path, fig_dir: Path) -> None:
    wave_df = read_wave_packets(files, UHF_GROUP)
    if wave_df.empty:
        return
    sample_rate = estimate_sample_rate(wave_df)
    packet_summary = wave_df.drop(columns=["samples"]).copy()
    packet_summary["estimated_sample_rate_hz"] = sample_rate
    safe_to_csv(packet_summary, plot_dir / "uhf_wave_packet_summary.csv")

    reps = representative_waveforms(wave_df, max_items=5)
    peak_rows: list[dict[str, object]] = []
    for idx, row in reps.reset_index(drop=True).iterrows():
        samples = row["samples"]
        spec = spectrum_from_samples(samples, sample_rate)
        tag = f"{idx + 1}_{datetime.fromtimestamp(float(row['time_s'])).strftime('%H%M%S_%f')}"
        spec_origin = spec.copy()
        spec_origin["frequency_mhz"] = spec_origin["frequency_hz"] / 1e6
        spec_origin = spec_origin[["frequency_hz", "frequency_mhz", "magnitude"]]
        safe_to_csv(spec_origin, plot_dir / f"origin_04_fft_uhf_{tag}.csv")
        safe_to_csv(spec, plot_dir / f"fft_spectrum_uhf_{tag}.csv")

        peaks = extract_main_frequencies(spec)
        for rank, (freq, mag) in enumerate(peaks, start=1):
            peak_rows.append(
                {
                    "datetime": row["datetime"],
                    "time_s": row["time_s"],
                    "elapsed_s": row["elapsed_s"],
                    "channel": row["channel"],
                    "rank": rank,
                    "frequency_hz": freq,
                    "frequency_mhz": freq / 1e6,
                    "magnitude": mag,
                }
            )

        fig, ax = plt.subplots(figsize=SMALL_FIGSIZE)
        ax.plot(spec["frequency_hz"] / 1e6, spec["magnitude"], lw=1.0)
        for freq, mag in peaks:
            ax.plot(freq / 1e6, mag, "o", ms=3, color="red")
        ax.set_xlabel("频率 (MHz)")
        ax.set_ylabel("幅值")
        ax.set_title(f"特高频 CH{row['channel']}  {datetime.fromtimestamp(float(row['time_s'])).strftime('%H:%M:%S.%f')[:-3]}", fontsize=9)
        ax.grid(True, alpha=0.25)
        save_figure(fig, fig_dir / f"04_uhf_fft_{tag}")

    peak_df = pd.DataFrame(peak_rows)
    if not peak_df.empty:
        safe_to_csv(peak_df, plot_dir / "uhf_main_frequencies.csv")
        safe_to_csv(make_origin_main_frequency_table(peak_df), plot_dir / "origin_05_uhf_main_frequency_evolution.csv")
        fig, ax = plt.subplots(figsize=WIDE_FIGSIZE)
        for rank, part in peak_df.groupby("rank"):
            ax.plot(part["datetime"], part["frequency_mhz"], "o-", ms=3, lw=1.0, label=f"主频 {rank}")
        ax.set_xlabel("时间")
        ax.set_ylabel("特高频主频 (MHz)")
        ax.grid(True, alpha=0.25)
        ax.legend(fontsize=8)
        fig.autofmt_xdate()
        save_figure(fig, fig_dir / "05_uhf_main_frequency_evolution")

def plot_fft_and_main_freqs(files: list[CapFile], plot_dir: Path, fig_dir: Path) -> None:
    summary_path = plot_dir / "uhf_wave_packet_summary.csv"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    sample_rates: list[float] = []
    candidates_by_minute: dict[pd.Timestamp, list[dict[str, object]]] = {}
    prev_by_event: dict[tuple[int, int], tuple[float, int]] = {}
    first_time: float | None = None
    fft_plot_dir = plot_dir / "fft_by_minute" / "uhf"
    fft_fig_dir = fig_dir / "04_fft_by_minute" / "uhf"
    fft_plot_dir.mkdir(parents=True, exist_ok=True)
    fft_fig_dir.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "time_s",
        "datetime",
        "elapsed_s",
        "signal_group",
        "signal_key",
        "signal_label",
        "feature",
        "channel",
        "sample_count",
        "mean_raw",
        "rms_raw",
        "peak_abs_raw",
        "event_sec",
        "event_frac",
        "source_file",
    ]
    with summary_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in iter_wave_packets(files, UHF_GROUP):
            ts = float(row["time_s"])
            if first_time is None:
                first_time = ts
            row["elapsed_s"] = ts - first_time
            key = (int(row["event_sec"]), int(row["event_frac"]))
            if key in prev_by_event:
                prev_time, prev_count = prev_by_event[key]
                dt = ts - prev_time
                if dt > 0 and len(sample_rates) < 500:
                    sample_rates.append(prev_count / dt)
            prev_by_event[key] = (ts, int(row["sample_count"]))

            writer.writerow({name: row.get(name) for name in fieldnames})

            candidate = dict(row)
            minute = pd.Timestamp(row["datetime"]).floor("min")
            minute_candidates = candidates_by_minute.setdefault(minute, [])
            if len(minute_candidates) < 20:
                minute_candidates.append(candidate)
            else:
                min_idx = min(range(len(minute_candidates)), key=lambda i: float(minute_candidates[i]["peak_abs_raw"]))
                if float(candidate["peak_abs_raw"]) > float(minute_candidates[min_idx]["peak_abs_raw"]):
                    minute_candidates[min_idx] = candidate

    if not candidates_by_minute:
        return

    sample_rate = float(np.median(sample_rates)) if sample_rates else 1.0
    peak_rows: list[dict[str, object]] = []
    index_rows: list[dict[str, object]] = []
    spectra_evolution_rows: list[pd.DataFrame] = []
    spectral_feature_rows: list[dict[str, object]] = []
    for minute, candidates in sorted(candidates_by_minute.items()):
        if not candidates:
            continue
        rows_sorted = sorted(candidates, key=lambda item: float(item["peak_abs_raw"]), reverse=True)
        row = pd.Series(rows_sorted[0])
        samples = row["samples"]
        spec = spectrum_from_samples(samples, sample_rate)
        minute_tag = pd.Timestamp(minute).strftime("%Y%m%d_%H%M")
        tag = f"04_fft_uhf_{minute_tag}"
        spec_for_evolution = spec.copy()
        spec_for_evolution["minute"] = pd.Timestamp(minute).strftime("%Y-%m-%d %H:%M:%S")
        spec_for_evolution["frequency_mhz"] = spec_for_evolution["frequency_hz"] / 1e6
        spectra_evolution_rows.append(spec_for_evolution[["minute", "frequency_hz", "frequency_mhz", "magnitude"]])
        spectral_feature_rows.append(spectrum_evolution_features(spec, pd.Timestamp(minute)))
        spec_origin = spec.copy()
        spec_origin["frequency_mhz"] = spec_origin["frequency_hz"] / 1e6
        spec_origin = spec_origin[["frequency_hz", "frequency_mhz", "magnitude"]]
        csv_path = safe_to_csv(spec_origin, fft_plot_dir / f"origin_{tag}.csv")
        safe_to_csv(spec, fft_plot_dir / f"fft_spectrum_uhf_{minute_tag}.csv")

        peaks = extract_main_frequencies(spec)
        for rank, (freq, mag) in enumerate(peaks, start=1):
            peak_rows.append(
                {
                    "datetime": row["datetime"],
                    "time_s": row["time_s"],
                    "elapsed_s": row["elapsed_s"],
                    "minute": pd.Timestamp(minute).strftime("%Y-%m-%d %H:%M:%S"),
                    "channel": row["channel"],
                    "rank": rank,
                    "frequency_hz": freq,
                    "frequency_mhz": freq / 1e6,
                    "magnitude": mag,
                }
            )

        fig, ax = plt.subplots(figsize=SMALL_FIGSIZE)
        ax.plot(spec["frequency_hz"] / 1e6, spec["magnitude"], lw=1.0)
        for freq, mag in peaks:
            ax.plot(freq / 1e6, mag, "o", ms=3, color="red")
        ax.set_xlabel("Frequency (MHz)")
        ax.set_ylabel("Magnitude")
        ax.set_title(f"UHF CH{row['channel']}  {pd.Timestamp(minute).strftime('%Y-%m-%d %H:%M')}", fontsize=9)
        ax.grid(True, alpha=0.25)
        fig_base = fft_fig_dir / tag
        save_figure(fig, fig_base)
        index_rows.append(
            {
                "signal_key": "uhf",
                "signal_label": "特高频",
                "minute": pd.Timestamp(minute).strftime("%Y-%m-%d %H:%M:%S"),
                "selected_time": row["datetime"],
                "channel": row["channel"],
                "peak_abs_raw": row["peak_abs_raw"],
                "csv_file": str(csv_path),
                "png_file": str(fig_base.with_suffix(".png")),
                "pdf_file": str(fig_base.with_suffix(".pdf")),
            }
        )

    peak_df = pd.DataFrame(peak_rows)
    spectra_long = pd.concat(spectra_evolution_rows, ignore_index=True) if spectra_evolution_rows else pd.DataFrame()
    feature_df = pd.DataFrame(spectral_feature_rows)
    plot_uhf_time_frequency_evolution(spectra_long, peak_df, feature_df, plot_dir, fig_dir)
    if not peak_df.empty:
        safe_to_csv(peak_df, fft_plot_dir / "uhf_main_frequencies_by_minute_long.csv")
        main_freq_wide = make_origin_main_frequency_table(peak_df)
        if "minute" not in main_freq_wide.columns and not peak_df.empty:
            minute_map = peak_df.drop_duplicates(["datetime", "elapsed_s"])[["datetime", "elapsed_s", "minute"]]
            main_freq_wide = main_freq_wide.merge(minute_map, on=["datetime", "elapsed_s"], how="left")
            cols = ["minute"] + [col for col in main_freq_wide.columns if col != "minute"]
            main_freq_wide = main_freq_wide[cols]
        safe_to_csv(main_freq_wide, fft_plot_dir / "origin_05_uhf_main_frequency_by_minute.csv")
        if index_rows:
            safe_to_csv(pd.DataFrame(index_rows), plot_dir / "origin_04_fft_by_minute_index.csv")
        fig, ax = plt.subplots(figsize=WIDE_FIGSIZE)
        for rank, part in peak_df.groupby("rank"):
            ax.plot(part["datetime"], part["frequency_mhz"], "o-", ms=3, lw=1.0, label=f"Peak {rank}")
        ax.set_xlabel("Time")
        ax.set_ylabel("UHF main frequency (MHz)")
        ax.grid(True, alpha=0.25)
        ax.legend(fontsize=8)
        fig.autofmt_xdate()
        save_figure(fig, fig_dir / "05_uhf_main_frequency_evolution_by_minute")


def write_inventory(files: list[CapFile], summary_dir: Path) -> None:
    rows = []
    for cap in files:
        count = 0
        lengths: dict[int, int] = {}
        for _ts, payload in iter_pcap_records(cap.path):
            count += 1
            lengths[len(payload)] = lengths.get(len(payload), 0) + 1
        common_lengths = "; ".join(f"{k}:{v}" for k, v in sorted(lengths.items(), key=lambda item: -item[1])[:8])
        rows.append(
            {
                "file": cap.path.name,
                "kind": cap.kind,
                "signal_group": cap.group,
                "signal_label": cap.signal_label,
                "channel": cap.channel,
                "feature": cap.feature,
                "records": count,
                "compressed_bytes": cap.path.stat().st_size,
                "common_payload_lengths": common_lengths,
            }
        )
    safe_to_csv(pd.DataFrame(rows), summary_dir / "file_inventory.csv")


def run(input_dir: Path, output_dir: Path) -> None:
    configure_matplotlib()
    plot_dir = output_dir / "plot_data"
    fig_dir = output_dir / "figures"
    summary_dir = output_dir / "summary"
    for directory in (plot_dir, fig_dir, summary_dir):
        directory.mkdir(parents=True, exist_ok=True)

    files = parse_cap_files(input_dir)
    write_inventory(files, summary_dir)

    chara_df = read_chara(files)
    safe_to_csv(chara_df, plot_dir / "feature_events.csv")

    amp_df, rate_df = make_time_series(chara_df)
    safe_to_csv(amp_df, plot_dir / "feature_amplitude_1s.csv")
    safe_to_csv(rate_df, plot_dir / "feature_repetition_rate_1s.csv")

    voltage_df = voltage_proxy(files)
    if not voltage_df.empty:
        safe_to_csv(voltage_df, plot_dir / "applied_voltage.csv")
        safe_to_csv(aggregate_voltage_1s(voltage_df), plot_dir / "applied_voltage_1s.csv")
    else:
        safe_to_csv(
            pd.DataFrame(
                [
                    {
                        "status": "not_found",
                        "reason": "No packets with protocol channel_type=8 (阀侧末屏电压) were found in the input cap files.",
                    }
                ]
            ),
            plot_dir / "applied_voltage_not_found.csv",
        )

    make_origin_time_table(
        amp_df,
        "amplitude_abs_median_raw",
        "幅值_raw",
        voltage_df,
    ).pipe(safe_to_csv, plot_dir / "origin_01_amplitude_voltage.csv")
    make_origin_time_table(
        rate_df,
        "repetition_rate_per_s",
        "重复率_1_per_s",
        voltage_df,
    ).pipe(safe_to_csv, plot_dir / "origin_02_repetition_voltage.csv")

    plot_amplitude(amp_df, voltage_df, fig_dir)
    plot_repetition(rate_df, voltage_df, fig_dir)
    plot_fft_and_main_freqs(files, plot_dir, fig_dir)
    gc.collect()
    plot_prpd(chara_df, plot_dir, fig_dir)
    gc.collect()

    notes = [
        "Parsing completed.",
        f"Input directory: {input_dir}",
        "Signal type mapping verified against 通讯协议.pdf and batch_pd_parser.py: 1=UHF/特高频, 2=HFCT_SCREEN/高频, 4=ACOUSTIC/超声.",
        "The second numeric field in file names is treated as channel number.",
        f"UHF FFT source: wave group {UHF_GROUP} (all available UHF channels).",
        "Applied voltage rule: only packets with protocol channel_type=8 (阀侧末屏电压) are treated as applied voltage.",
        "For the current 0417 input, no channel_type=8 packets were found; previous up_down offset-40 voltage proxy was removed because that field is decay_ps in 0xf014 packets.",
        "Origin-ready aligned tables are named origin_*.csv in plot_data.",
        "Figure sizes: wide 18 cm x 11 cm, small 9 cm x 5.5 cm.",
    ]
    (summary_dir / "analysis_notes.txt").write_text("\n".join(notes), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze discharge experiment PCAP gzip data.")
    parser.add_argument("--input", type=Path, default=Path(r"D:\BaiduNetdiskDownload\0417\0417"))
    parser.add_argument("--output", type=Path, default=Path(r"D:\BaiduNetdiskDownload\0417\discharge_analysis\outputs_checked"))
    args = parser.parse_args()
    run(args.input, args.output)


if __name__ == "__main__":
    main()
