function tek_prpd_monitor(varargin)
%TEK_PRPD_MONITOR Tektronix acquisition and PRPD drawing based on ./code.
%
% This version follows the logic in the code folder:
%   1. Put the scope in STOPAfter SEQuence mode.
%   2. Wait for one acquisition sequence to finish.
%   3. Read CH3 voltage and CH2 PD/HFCT by binary CURVE? transfer.
%   4. Treat every abs(CH2) sample above a fixed amplitude threshold as PD.
%   5. Use the first upward zero crossing of CH3 as the 0 deg phase reference.
%   6. Draw PRPD using ksdensity-based point color, as in SignalProcessor.m.
%
% Added for this experiment:
%   - Timestamped result folder.
%   - Periodic PRPD PNG/MAT saving.
%   - Cumulative PRPD PNG/MAT saving at the end.

cfg = parseInputs(varargin{:});

startStamp = datestr(now, 'yyyymmdd_HHMMSS');
if ~exist(cfg.ResultRoot, 'dir')
    mkdir(cfg.ResultRoot);
end
outputDir = fullfile(cfg.ResultRoot, ['PRPD_' startStamp]);
if ~exist(outputDir, 'dir')
    mkdir(outputDir);
end

fprintf('PRPD output directory: %s\n', outputDir);
fprintf('Scope address: %s\n', cfg.Addr);
fprintf('Acquisition points: %d\n', cfg.AcquisitionPoints);
fprintf('PD threshold: %.6g V\n', cfg.ProcessingThreshold);

scope = [];
fig = createPrpdFigure(cfg);
cleanupObj = onCleanup(@cleanup);
rmsLogFile = fullfile(outputDir, 'ch3_rms_log.xlsx');

intervalStorage = {};
intervalPhases = [];
intervalAmps = [];
intervalSegmentTime = 0;
intervalStart = tic;

cumulativeStorage = {};
cumulativePhases = [];
cumulativeAmps = [];
cumulativeSegmentTime = 0;

acqCount = 0;
sessionStart = tic;
rmsLog = initRmsLog();
lastRmsFlush = tic;

try
    scope = visadev(cfg.Addr);
    scope.ByteOrder = 'big-endian';
    scope.Timeout = cfg.TimeoutSec;
    setBufferIfAvailable(scope, cfg.InputBufferSize);

    deviceId = strtrim(writeread(scope, '*IDN?'));
    if isempty(deviceId)
        error('Scope did not respond to *IDN?.');
    end
    fprintf('Connected: %s\n', deviceId);
    saveSessionInfo(outputDir, deviceId, cfg);
    rmsLog = flushRmsLog(rmsLogFile, rmsLog);
    lastRmsFlush = tic;

    writeline(scope, 'ACQuire:STOPAfter SEQuence');
    pause(0.1);
    writeline(scope, 'ACQuire:STATE RUN');
    pause(0.1);

    fprintf('Acquiring. Press Ctrl+C to stop.\n');
    while cfg.DurationMinutes <= 0 || toc(sessionStart) < cfg.DurationMinutes * 60
        waitForSequenceComplete(scope, cfg);

        [pdSignal, voltageSignal, timeVector] = fetchSignals(scope, cfg);
        acqCount = acqCount + 1;

        acquisitionDuration = timeVector(end) - timeVector(1);
        if acquisitionDuration <= 0 || ~isfinite(acquisitionDuration)
            acquisitionDuration = numel(timeVector) * median(diff(timeVector));
        end

        ch3Rms = rmsCompat(voltageSignal);
        rmsLog = appendRmsLog(rmsLog, acqCount, toc(sessionStart), ch3Rms, acquisitionDuration);
        if toc(lastRmsFlush) >= cfg.RmsFlushIntervalSec
            rmsLog = flushRmsLog(rmsLogFile, rmsLog);
            lastRmsFlush = tic;
        end

        [pdAmps, phases] = calculateVoltagePrpd( ...
            pdSignal, voltageSignal, timeVector, cfg.ProcessingThreshold, ...
            cfg.PowerFreqHz, cfg.CutoffFreqVoltage, cfg.HysteresisFactor);

        pdAmpsRaw = pdAmps(:)';
        phases = mod(phases(:)' + cfg.PhaseOffsetDeg, 360);

        row = makeStorageRow(pdSignal, voltageSignal, timeVector, toc(sessionStart));
        intervalStorage(end+1, :) = row; %#ok<AGROW>
        cumulativeStorage(end+1, :) = row; %#ok<AGROW>

        intervalPhases = [intervalPhases, phases]; %#ok<AGROW>
        intervalAmps = [intervalAmps, pdAmpsRaw]; %#ok<AGROW>
        intervalSegmentTime = intervalSegmentTime + acquisitionDuration;

        cumulativePhases = [cumulativePhases, phases]; %#ok<AGROW>
        cumulativeAmps = [cumulativeAmps, pdAmpsRaw]; %#ok<AGROW>
        cumulativeSegmentTime = cumulativeSegmentTime + acquisitionDuration;

        fprintf('Acq %d: samples=%d, PD points=%d, elapsed=%.1f s\n', ...
            acqCount, numel(pdSignal), numel(phases), toc(sessionStart));

        if toc(intervalStart) >= cfg.PlotIntervalSec
            rmsLog = flushRmsLog(rmsLogFile, rmsLog);
            lastRmsFlush = tic;
            savePrpdInterval(outputDir, fig, cfg, intervalStorage, intervalPhases, ...
                intervalAmps, intervalSegmentTime, false);
            intervalStorage = {};
            intervalPhases = [];
            intervalAmps = [];
            intervalSegmentTime = 0;
            intervalStart = tic;
        end

        writeline(scope, 'ACQuire:STATE RUN');
        pause(cfg.PollIntervalSec);
    end

    rmsLog = flushRmsLog(rmsLogFile, rmsLog);
    savePrpdInterval(outputDir, fig, cfg, intervalStorage, intervalPhases, ...
        intervalAmps, intervalSegmentTime, false);
    savePrpdInterval(outputDir, fig, cfg, cumulativeStorage, cumulativePhases, ...
        cumulativeAmps, cumulativeSegmentTime, true);

catch ME
    fprintf(2, 'Error: %s\n', ME.message);
    try
        rmsLog = flushRmsLog(rmsLogFile, rmsLog);
        savePrpdInterval(outputDir, fig, cfg, intervalStorage, intervalPhases, ...
            intervalAmps, intervalSegmentTime, false);
        savePrpdInterval(outputDir, fig, cfg, cumulativeStorage, cumulativePhases, ...
            cumulativeAmps, cumulativeSegmentTime, true);
    catch saveError
        fprintf(2, 'Save after error failed: %s\n', saveError.message);
    end
end

    function cleanup()
        if ~isempty(scope)
            try
                clear scope
            catch
            end
        end
        fprintf('Session ended. Acquisitions: %d\n', acqCount);
    end
end

function cfg = parseInputs(varargin)
p = inputParser;
p.addParameter('Addr', 'USB0::0x0699::0x0401::B010503::INSTR', @ischar);
p.addParameter('ResultRoot', fullfile(pwd, 'PRPD_results'), @ischar);
p.addParameter('DurationMinutes', 5, @(x) isnumeric(x) && isscalar(x) && x >= 0);
p.addParameter('PlotIntervalSec', 60, @(x) isnumeric(x) && isscalar(x) && x > 0);
p.addParameter('PollIntervalSec', 0.01, @(x) isnumeric(x) && isscalar(x) && x >= 0);
p.addParameter('TimeoutSec', 60, @(x) isnumeric(x) && isscalar(x) && x >= 10);
p.addParameter('AcquisitionPoints', [], @(x) isempty(x) || (isnumeric(x) && isscalar(x) && x > 0));
p.addParameter('MaxPointsPerRead', [], @(x) isempty(x) || (isnumeric(x) && isscalar(x) && x > 0));
p.addParameter('ProcessingThreshold', 0.002, @(x) isnumeric(x) && isscalar(x) && x >= 0);
p.addParameter('MinPulseAbsV', [], @(x) isempty(x) || (isnumeric(x) && isscalar(x) && x >= 0));
p.addParameter('PowerFreqHz', 50, @(x) isnumeric(x) && isscalar(x) && x > 0);
p.addParameter('PhaseOffsetDeg', 0, @(x) isnumeric(x) && isscalar(x));
p.addParameter('CalibrationFactor', 12500, @(x) isnumeric(x) && isscalar(x) && x > 0);
p.addParameter('SignalLabel', '高频', @ischar);
p.addParameter('CutoffFreqVoltage', 2000, @(x) isnumeric(x) && isscalar(x) && x >= 0);
p.addParameter('HysteresisFactor', 0.05, @(x) isnumeric(x) && isscalar(x) && x >= 0);
p.addParameter('InputBufferSize', 10000000, @(x) isnumeric(x) && isscalar(x) && x > 0);
p.addParameter('FigureVisible', true, @(x) islogical(x) || isnumeric(x));
p.addParameter('RmsFlushIntervalSec', 5, @(x) isnumeric(x) && isscalar(x) && x > 0);

% Accepted for compatibility with main_prpd_logger; not used by code-folder logic.
p.addParameter('ThresholdSigma', [], @(x) isempty(x) || isnumeric(x));
p.addParameter('MinPulseDistanceSec', [], @(x) isempty(x) || isnumeric(x));
p.addParameter('PhaseBinDeg', [], @(x) isempty(x) || isnumeric(x));
p.addParameter('AmplitudeBins', [], @(x) isempty(x) || isnumeric(x));
p.addParameter('SaveEachWaveform', [], @(x) isempty(x) || islogical(x) || isnumeric(x));
p.addParameter('UseAcqCounter', [], @(x) isempty(x) || islogical(x) || isnumeric(x));

p.parse(varargin{:});
cfg = p.Results;

if isempty(cfg.AcquisitionPoints)
    if isempty(cfg.MaxPointsPerRead)
        cfg.AcquisitionPoints = 1e6;
    else
        cfg.AcquisitionPoints = cfg.MaxPointsPerRead;
    end
end
cfg.AcquisitionPoints = round(cfg.AcquisitionPoints);

if ~isempty(cfg.MinPulseAbsV) && cfg.MinPulseAbsV > 0
    cfg.ProcessingThreshold = cfg.MinPulseAbsV;
end
cfg.FigureVisible = logical(cfg.FigureVisible);
end

function waitForSequenceComplete(scope, cfg)
t0 = tic;
while true
    status = strtrim(writeread(scope, 'ACQuire:STATE?'));
    statusValue = str2double(status);
    if ~isnan(statusValue) && statusValue == 0
        return;
    end
    if toc(t0) > cfg.TimeoutSec
        error('Timed out waiting for acquisition sequence to complete.');
    end
    pause(0.01);
end
end

function [pdSignal, voltageSignal, timeVector] = fetchSignals(scope, cfg)
writeline(scope, 'DATa:ENCdg RIBinary');
writeline(scope, 'DATa:WIDth 2');
writeline(scope, 'DATa:STARt 1');
writeline(scope, ['DATa:STOP ' num2str(cfg.AcquisitionPoints)]);

pdSignal = [];
voltageSignal = [];
timeZero = [];
timeIncrement = [];

for ch = [3, 2]
    chStr = ['CH' num2str(ch)];
    writeline(scope, ['DATa:SOUrce ' chStr]);
    yZero = str2double(writeread(scope, 'WFMPRE:YZERO?'));
    yMultiplier = str2double(writeread(scope, 'WFMPRE:YMULT?'));
    yOffset = str2double(writeread(scope, 'WFMPRE:YOFF?'));
    if isempty(timeZero)
        timeZero = str2double(writeread(scope, 'WFMPRE:XZERO?'));
        timeIncrement = str2double(writeread(scope, 'WFMPRE:XINCR?'));
    end

    writeline(scope, 'CURVE?');
    rawData = readbinblock(scope, 'int16');
    if numel(rawData) < cfg.AcquisitionPoints * 0.9
        writeline(scope, 'CURVE?');
        rawData = readbinblock(scope, 'int16');
    end
    scaledData = yZero + yMultiplier * (double(rawData(:)) - yOffset);

    if ch == 3
        voltageSignal = scaledData;
    elseif ch == 2
        pdSignal = scaledData;
    end
end

dataLength = min(numel(voltageSignal), numel(pdSignal));
if dataLength < 10
    error('Not enough waveform points returned.');
end
voltageSignal = voltageSignal(1:dataLength);
pdSignal = pdSignal(1:dataLength);
timeVector = timeZero + (0:dataLength-1)' * timeIncrement;
end

function row = makeStorageRow(pdSignal, voltageSignal, timeVector, elapsed)
row = cell(1, 8);
row{1} = pdSignal;
row{2} = voltageSignal;
row{3} = [];
row{4} = timeVector;
row{5} = elapsed;
row{6} = rmsCompat(voltageSignal);
row{7} = max(abs(pdSignal));
row{8} = [];
end

function [pdAmplitudesVoltage, phasesVoltage] = calculateVoltagePrpd( ...
    pdSignal, voltageSignal, timeVector, amplitudeThreshold, voltageFrequency, ...
    cutoffFreqVoltage, hystFactorVoltage)

validPdIndices = find(abs(pdSignal) > amplitudeThreshold);
validPdCount = numel(validPdIndices);
if validPdCount == 0
    pdAmplitudesVoltage = [];
    phasesVoltage = [];
    return;
end

pdTimes = timeVector(validPdIndices);
[crossingTimes, crossingDirs] = findZeroCrossings( ...
    timeVector, voltageSignal, mean(voltageSignal), cutoffFreqVoltage, hystFactorVoltage);
positiveCrossingIndices = find(crossingDirs == -1);

pdAmplitudesVoltage = pdSignal(validPdIndices)';
phasesVoltage = zeros(1, validPdCount);
if ~isempty(positiveCrossingIndices)
    firstPosCrossTime = crossingTimes(positiveCrossingIndices(1));
    phasesVoltage = mod((pdTimes - firstPosCrossTime) / (1 / voltageFrequency) * 360, 360);
end
end

function [crossingTimes, crossingDirs] = findZeroCrossings( ...
    timeData, signalData, thresholdValue, cutoffFreq, hystFactor)

crossingTimes = [];
crossingDirs = [];
if numel(timeData) < 3
    return;
end

if cutoffFreq > 0
    fs = 1 / (timeData(2) - timeData(1));
    wn = cutoffFreq / (fs / 2);
    if wn > 0 && wn < 1
        [b, a] = butter(4, wn, 'low');
        signalData = filtfilt(b, a, signalData);
    end
end

normalizedSignal = signalData - thresholdValue;
if hystFactor > 0
    rmsVal = rmsCompat(normalizedSignal);
    posThresh = hystFactor * rmsVal;
    negThresh = -posThresh;

    state = 'mid';
    if normalizedSignal(1) > posThresh
        state = 'high';
    elseif normalizedSignal(1) < negThresh
        state = 'low';
    end

    for i = 2:numel(normalizedSignal)
        prev = normalizedSignal(i-1);
        curr = normalizedSignal(i);
        timePrev = timeData(i-1);
        timeCurr = timeData(i);

        if strcmp(state, 'high')
            if curr < negThresh
                crossingTime = timePrev + (0 - prev) / (curr - prev) * (timeCurr - timePrev);
                crossingTimes = [crossingTimes, crossingTime]; %#ok<AGROW>
                crossingDirs = [crossingDirs, 1]; %#ok<AGROW>
                state = 'low';
            end
        elseif strcmp(state, 'low')
            if curr > posThresh
                crossingTime = timePrev + (0 - prev) / (curr - prev) * (timeCurr - timePrev);
                crossingTimes = [crossingTimes, crossingTime]; %#ok<AGROW>
                crossingDirs = [crossingDirs, -1]; %#ok<AGROW>
                state = 'high';
            end
        elseif strcmp(state, 'mid')
            if curr > posThresh
                state = 'high';
            elseif curr < negThresh
                state = 'low';
            end
        end
    end
else
    signPrev = sign(normalizedSignal(1:end-1));
    signNext = sign(normalizedSignal(2:end));
    suspectIndices = find(signPrev .* signNext <= 0);
    for i = 1:numel(suspectIndices)
        idx = suspectIndices(i);
        timeLeft = timeData(idx);
        timeRight = timeData(idx + 1);
        signalLeft = normalizedSignal(idx);
        signalRight = normalizedSignal(idx + 1);
        if signalLeft == 0
            crossingTime = timeLeft;
        elseif signalRight == 0
            crossingTime = timeRight;
        else
            crossingTime = timeLeft + (0 - signalLeft) / (signalRight - signalLeft) * (timeRight - timeLeft);
        end
        crossingTimes = [crossingTimes, crossingTime]; %#ok<AGROW>
        crossingDirs = [crossingDirs, signPrev(idx)]; %#ok<AGROW>
    end
end
end

function savePrpdInterval(outputDir, fig, cfg, dataStorage, phases, amplitudesRaw, segmentTime, isCumulative)
if isempty(dataStorage) && isempty(phases)
    return;
end

stamp = datestr(now, 'yyyymmdd_HHMMSS');
displayStamp = datestr(now, 'yyyy-mm-dd HH:MM');
pointCount = numel(phases);
if isCumulative
    prefix = ['PRPD_cumulative_' stamp];
    titleText = sprintf('%s cumulative %s', cfg.SignalLabel, displayStamp);
else
    prefix = ['PRPD_' stamp];
    titleText = sprintf('%s %s', cfg.SignalLabel, displayStamp);
end

drawPrpd(fig, phases, amplitudesRaw, segmentTime, cfg, titleText);
saveas(fig, fullfile(outputDir, [prefix '.png']));

data_storage = dataStorage; %#ok<NASGU>
phases_voltage = phases; %#ok<NASGU>
pd_amplitudes_voltage_raw = amplitudesRaw; %#ok<NASGU>
total_segment_time = segmentTime; %#ok<NASGU>
[repetition_rate_matrix, phase_edges_deg, amplitude_edges_raw, event_table] = ...
    buildPrpdData(phases, amplitudesRaw, segmentTime, cfg); %#ok<NASGU>
save(fullfile(outputDir, [prefix '.mat']), ...
    'data_storage', 'phases_voltage', 'pd_amplitudes_voltage_raw', ...
    'total_segment_time', 'cfg', 'repetition_rate_matrix', ...
    'phase_edges_deg', 'amplitude_edges_raw', 'event_table', '-v7.3');

fprintf('Saved %s (%d PD points, %.3f s segment time)\n', ...
    [prefix '.png'], numel(phases), segmentTime);
end

function fig = createPrpdFigure(cfg)
if cfg.FigureVisible
    visible = 'on';
else
    visible = 'off';
end
fig = figure('Name', 'PRPD Monitor', ...
    'Color', 'w', ...
    'Visible', visible, ...
    'Position', [100, 120, 760, 560]);
end

function drawPrpd(fig, phases, amplitudesRaw, segmentTime, cfg, titleText)
figure(fig);
clf(fig);

if isempty(phases) || isempty(amplitudesRaw) || segmentTime <= 0
    ax = axes('Parent', fig, 'Position', [0.12 0.14 0.72 0.70]);
    set(ax, 'Color', [0.93 0.93 0.92], 'FontSize', 11);
    title(titleText);
    xlabel('相位 (deg)');
    ylabel('幅值 (raw)');
    xlim([0 360]);
    xticks(0:45:360);
    grid(ax, 'on');
    ax.XMinorTick = 'on';
    ax.YMinorTick = 'on';
    drawnow;
    return;
end

[~, ~, ~, eventTable] = ...
    buildPrpdData(phases, amplitudesRaw, segmentTime, cfg);

ax = axes('Parent', fig, 'Position', [0.12 0.14 0.72 0.70]);
hold(ax, 'on');
set(ax, 'Color', [0.93 0.93 0.92], 'Layer', 'top', 'FontSize', 11);

maxAbsAmp = max(abs(amplitudesRaw(:)));
if isempty(maxAbsAmp) || maxAbsAmp <= 0 || ~isfinite(maxAbsAmp)
    maxAbsAmp = 1;
end
yLim = 1.1 * maxAbsAmp * [-1, 1];

phaseVals = linspace(0, 360, 721);
sineRef = 0.9 * maxAbsAmp * sin(phaseVals * 2 * pi / 360);
plot(ax, phaseVals, sineRef, 'Color', [0.12 0.70 0.22], 'LineWidth', 1.4);

scatter(ax, eventTable.phase_deg, eventTable.amplitude_raw, 12, ...
    eventTable.repetition_rate_per_s, 'o', ...
    'filled', ...
    'MarkerFaceAlpha', 0.80, ...
    'MarkerEdgeColor', 'none');

colormap(ax, jet);
c = colorbar(ax);
ylabel(c, '重复率 (1/s)');
maxRate = max(eventTable.repetition_rate_per_s);
if isempty(maxRate) || maxRate <= 0 || ~isfinite(maxRate)
    maxRate = 1;
end
clim(ax, [0 maxRate]);
c.Ticks = linspace(0, maxRate, 5);

xlabel(ax, '相位 (deg)');
ylabel(ax, '幅值 (raw)');
title(ax, titleText, 'FontWeight', 'normal');
xlim(ax, [0 360]);
ylim(ax, yLim);
xticks(ax, 0:45:360);
grid(ax, 'on');
box(ax, 'on');
ax.XMinorTick = 'on';
ax.YMinorTick = 'on';
ax.GridColor = [0.62 0.62 0.62];
ax.GridAlpha = 0.55;
ax.MinorGridColor = [0.72 0.72 0.72];
ax.MinorGridAlpha = 0.35;
grid(ax, 'minor');

periodMs = 1000 / cfg.PowerFreqHz;
topAx = axes('Parent', fig, ...
    'Position', ax.Position, ...
    'Color', 'none', ...
    'XAxisLocation', 'top', ...
    'YAxisLocation', 'right', ...
    'YTick', [], ...
    'Box', 'off', ...
    'XLim', [0 360], ...
    'FontSize', 11);
topTicksMs = 0:2:periodMs;
topAx.XTick = topTicksMs / periodMs * 360;
topAx.XTickLabel = cellstr(num2str(topTicksMs(:), '%.0f'));
xlabel(topAx, '工频周期时间 (ms)');
uistack(topAx, 'top');
linkaxes([ax topAx], 'x');
drawnow;
end

function saveSessionInfo(outputDir, deviceId, cfg)
fid = fopen(fullfile(outputDir, 'session_info.txt'), 'w');
if fid < 0
    return;
end
cleaner = onCleanup(@() fclose(fid)); %#ok<NASGU>
fprintf(fid, 'StartTime: %s\n', datestr(now, 'yyyy-mm-dd HH:MM:SS'));
fprintf(fid, 'ScopeIDN: %s\n', deviceId);
fprintf(fid, 'LogicSource: code folder SignalAcquisition/SignalFetcher/PhaseCalculator/SignalProcessor\n\n');
names = fieldnames(cfg);
for i = 1:numel(names)
    val = cfg.(names{i});
    if isnumeric(val) || islogical(val)
        valText = mat2str(val);
    elseif ischar(val)
        valText = val;
    else
        valText = '<unsupported>';
    end
    fprintf(fid, '%s: %s\n', names{i}, valText);
end
end

function setBufferIfAvailable(scope, bufferSize)
if isprop(scope, 'InputBufferSize')
    scope.InputBufferSize = bufferSize;
end
if isprop(scope, 'OutputBufferSize')
    scope.OutputBufferSize = bufferSize;
end
end

function y = rmsCompat(x)
x = double(x(:));
y = sqrt(mean(x .^ 2));
end

function logStruct = initRmsLog()
logStruct = struct();
logStruct.AcquisitionIndex = zeros(0, 1);
logStruct.ElapsedSec = zeros(0, 1);
logStruct.Timestamp = cell(0, 1);
logStruct.CH3_RMS = zeros(0, 1);
logStruct.SegmentDurationSec = zeros(0, 1);
logStruct.LastFlushedCount = -1;
end

function logStruct = appendRmsLog(logStruct, acquisitionIndex, elapsedSec, ch3Rms, segmentDurationSec)
logStruct.AcquisitionIndex(end + 1, 1) = acquisitionIndex;
logStruct.ElapsedSec(end + 1, 1) = elapsedSec;
logStruct.Timestamp{end + 1, 1} = datestr(now, 'yyyy-mm-dd HH:MM:SS.FFF');
logStruct.CH3_RMS(end + 1, 1) = ch3Rms;
logStruct.SegmentDurationSec(end + 1, 1) = segmentDurationSec;
end

function logStruct = flushRmsLog(filePath, logStruct)
if logStruct.LastFlushedCount == numel(logStruct.AcquisitionIndex)
    return;
end
T = table( ...
    logStruct.AcquisitionIndex, ...
    logStruct.ElapsedSec, ...
    logStruct.Timestamp, ...
    logStruct.CH3_RMS, ...
    logStruct.SegmentDurationSec, ...
    'VariableNames', { ...
        'AcquisitionIndex', ...
        'ElapsedSec', ...
        'Timestamp', ...
        'CH3_RMS', ...
        'SegmentDurationSec'});
try
    writetable(T, filePath);
    logStruct.LastFlushedCount = numel(logStruct.AcquisitionIndex);
catch ME
    fprintf(2, 'CH3 RMS log write failed: %s\n', ME.message);
end
end

function [matrix, phaseEdges, ampEdges, eventTable] = buildPrpdData(phases, amplitudesRaw, segmentTime, cfg)
phaseValues = double(phases(:));
ampValues = double(amplitudesRaw(:));
if isempty(phaseValues) || isempty(ampValues)
    phaseEdges = 0:5:360;
    ampEdges = linspace(-1, 1, 401);
    matrix = zeros(numel(ampEdges) - 1, numel(phaseEdges) - 1);
    eventTable = table();
    return;
end

maxAmp = max(abs(ampValues));
if ~isfinite(maxAmp) || maxAmp <= 0
    maxAmp = 1;
end

phaseEdges = 0:5:360;
if phaseEdges(end) ~= 360
    phaseEdges(end + 1) = 360; %#ok<AGROW>
end
ampEdges = linspace(-maxAmp, maxAmp, 401);

histCounts = histcounts2(phaseValues, ampValues, phaseEdges, ampEdges);
duration = max(segmentTime, 1.0);
matrix = histCounts' / duration;

phaseIdx = discretize(phaseValues, phaseEdges);
ampIdx = discretize(ampValues, ampEdges);
phaseIdx(phaseValues == 360) = numel(phaseEdges) - 1;
phaseIdx = min(max(phaseIdx, 1), numel(phaseEdges) - 1);
ampIdx = min(max(ampIdx, 1), numel(ampEdges) - 1);

repRate = zeros(size(phaseValues));
for i = 1:numel(phaseValues)
    repRate(i) = matrix(ampIdx(i), phaseIdx(i));
end

phaseTimeMs = phaseValues / 360.0 * (1000.0 / cfg.PowerFreqHz);
eventTable = table( ...
    phaseValues, ...
    phaseTimeMs, ...
    ampValues, ...
    abs(ampValues), ...
    phaseEdges(phaseIdx)', ...
    phaseEdges(phaseIdx + 1)', ...
    ampEdges(ampIdx)', ...
    ampEdges(ampIdx + 1)', ...
    repRate, ...
    'VariableNames', { ...
        'phase_deg', ...
        'phase_time_ms', ...
        'amplitude_raw', ...
        'amplitude_abs_raw', ...
        'phase_bin_left_deg', ...
        'phase_bin_right_deg', ...
        'amplitude_bin_left_raw', ...
        'amplitude_bin_right_raw', ...
        'repetition_rate_per_s'});
end

function r = rangeCompat(x)
x = x(:);
if isempty(x)
    r = 0;
else
    r = max(x) - min(x);
end
if r == 0
    r = 1;
end
end
