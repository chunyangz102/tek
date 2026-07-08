# Breakdown Trend Monitor Design

## Goal

Create a new MATLAB monitoring program for the inter-turn bubble discharge experiment that helps identify inception voltage and breakdown voltage. The program starts plotting when it starts running, refreshes the trend plot after each oscilloscope acquisition, and saves the measured trend data continuously for later processing.

## Assumptions

- The existing PRPD programs are known-good and should not be changed.
- Tektronix oscilloscope access continues to use `visadev`.
- Channel convention remains:
  - `CH3`: synchronized applied voltage signal.
  - `CH2`: PD/HFCT discharge signal.
- The discharge amplitude threshold uses the same role as `ProcessingThreshold` in `tek_prpd_monitor.m`.
- Discharge frequency and maximum discharge amplitude are computed only from samples where `abs(CH2) > threshold`.
- If one acquisition contains no valid discharge samples, discharge frequency and maximum discharge amplitude are recorded as `0`.

## Proposed Files

- `main_breakdown_logger.m`
  - User-facing entry point.
  - Defines oscilloscope address, acquisition length, run duration, refresh interval, save interval, and discharge threshold.
  - Calls `tek_breakdown_monitor`.

- `tek_breakdown_monitor.m`
  - Connects to the oscilloscope.
  - Uses single-sequence acquisition and binary waveform readout copied from the working PRPD monitor behavior.
  - Reads `CH3` and `CH2`.
  - Calculates trend metrics after each acquisition.
  - Updates the live figure.
  - Writes data continuously to disk.

- `tests/test_breakdown_metrics.m`
  - Tests pure metric calculations without requiring oscilloscope hardware.

## Metric Definitions

For each completed acquisition:

- `ElapsedSec`: wall-clock elapsed time since program start.
- `CH3_RMS`: `sqrt(mean(CH3.^2))`.
- `ValidPulseCount`: number of samples satisfying `abs(CH2) > ProcessingThreshold`.
- `AcquisitionDurationSec`: `timeVector(end) - timeVector(1)`, with a fallback based on sample spacing if needed.
- `DischargeRate_Hz`: `ValidPulseCount / AcquisitionDurationSec`.
- `MaxDischargeAbs_V`: `max(abs(valid CH2 samples))`, or `0` when `ValidPulseCount == 0`.

This design deliberately counts threshold-crossing samples, matching the current PRPD monitor's threshold logic. It does not add peak grouping or pulse de-duplication unless requested later.

## Live Plot

The monitor creates one figure with elapsed time on the x-axis.

- Left y-axis: `CH3_RMS`.
- Right y-axis: `DischargeRate_Hz` and `MaxDischargeAbs_V`.
- The figure updates after each acquisition.
- A legend identifies all three curves.
- The latest figure is saved periodically as `breakdown_trend.png`.

If scale differences make the two right-axis curves hard to read, a later revision can switch to three vertically stacked subplots. The first implementation keeps one shared time plot to match the request directly.

## Data Persistence

Each run creates a timestamped output directory under `breakdown_results`, for example:

```text
breakdown_results/Breakdown_20260708_153000
```

The monitor writes:

- `session_info.txt`: start time, oscilloscope ID, and configuration.
- `breakdown_log.xlsx`: table for later review in spreadsheet tools.
- `breakdown_log.mat`: MATLAB table and configuration for later processing.
- `breakdown_trend.png`: latest trend plot.

Data is flushed after each acquisition, so an interrupted experiment keeps all completed acquisition records.

## Error Handling

- If the oscilloscope does not respond to `*IDN?`, the program stops with a clear error.
- If an acquisition times out, the program saves all data collected so far before exiting.
- Cleanup releases the oscilloscope object.
- Existing PRPD files are not modified or used as dependencies beyond copying their known-good acquisition approach.

## Verification

- Add unit tests for the metric calculation:
  - Normal case with valid discharge samples.
  - No discharge samples above threshold.
  - Duration fallback when the time vector has invalid range.
- Run MATLAB tests if a MATLAB test runner is available.
- Run `checkcode` on new MATLAB files if MATLAB is available.
- Because oscilloscope hardware is required for full end-to-end verification, hardware connection will be reported separately if it cannot be run in the current environment.
