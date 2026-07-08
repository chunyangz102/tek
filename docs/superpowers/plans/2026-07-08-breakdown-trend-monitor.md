# Breakdown Trend Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a new MATLAB trend monitor that plots CH3 RMS, discharge frequency, and maximum discharge amplitude versus experiment time while continuously saving data.

**Architecture:** Keep the existing PRPD scripts unchanged. Add a small pure metric function that is easy to test, then add a new Tektronix monitor that reuses the proven single-sequence binary waveform readout pattern from the current PRPD monitor. Add a thin entry script for experiment configuration.

**Tech Stack:** MATLAB, `visadev`, Tektronix SCPI commands, `matlab.unittest`, table/MAT/XLSX output.

---

## File Structure

- Create `compute_breakdown_metrics.m`
  - Pure function for one acquisition's derived metrics.
  - No hardware, plotting, or file I/O.

- Create `tests/test_breakdown_metrics.m`
  - MATLAB unit tests for `compute_breakdown_metrics`.

- Create `tek_breakdown_monitor.m`
  - Hardware monitor, live plot, and continuous saving.
  - Contains private helper functions for input parsing, sequence wait, binary waveform readout, plotting, and log flushing.

- Create `main_breakdown_logger.m`
  - User-facing experiment entry point.
  - Mirrors the style of `main_prpd_logger.m`, but calls the breakdown trend monitor.

- Do not modify `main_prpd_logger.m`, `tek_prpd_monitor.m`, or files under `code/`.

---

### Task 1: Pure Metric Function

**Files:**
- Create: `compute_breakdown_metrics.m`
- Test: `tests/test_breakdown_metrics.m`

- [ ] **Step 1: Write the failing test file**

Create `tests/test_breakdown_metrics.m`:

```matlab
classdef test_breakdown_metrics < matlab.unittest.TestCase
    methods (Test)
        function computesMetricsForValidPulses(testCase)
            pdSignal = [0; 0.004; -0.006; 0.001];
            voltageSignal = [1; -1; 1; -1];
            timeVector = [0; 0.001; 0.002; 0.003];
            threshold = 0.003;

            metrics = compute_breakdown_metrics(pdSignal, voltageSignal, timeVector, threshold);

            testCase.verifyEqual(metrics.CH3_RMS, 1, 'AbsTol', 1e-12);
            testCase.verifyEqual(metrics.ValidPulseCount, 2);
            testCase.verifyEqual(metrics.AcquisitionDurationSec, 0.003, 'AbsTol', 1e-12);
            testCase.verifyEqual(metrics.DischargeRate_Hz, 2 / 0.003, 'AbsTol', 1e-9);
            testCase.verifyEqual(metrics.MaxDischargeAbs_V, 0.006, 'AbsTol', 1e-12);
        end

        function returnsZeroWhenNoPulseExceedsThreshold(testCase)
            pdSignal = [0; 0.001; -0.002; 0.0025];
            voltageSignal = [3; 4; 0; 0];
            timeVector = [0; 0.01; 0.02; 0.03];
            threshold = 0.003;

            metrics = compute_breakdown_metrics(pdSignal, voltageSignal, timeVector, threshold);

            testCase.verifyEqual(metrics.CH3_RMS, sqrt(25 / 4), 'AbsTol', 1e-12);
            testCase.verifyEqual(metrics.ValidPulseCount, 0);
            testCase.verifyEqual(metrics.AcquisitionDurationSec, 0.03, 'AbsTol', 1e-12);
            testCase.verifyEqual(metrics.DischargeRate_Hz, 0);
            testCase.verifyEqual(metrics.MaxDischargeAbs_V, 0);
        end

        function usesSampleSpacingWhenDurationIsInvalid(testCase)
            pdSignal = [0; 0.004; 0.005; 0];
            voltageSignal = [2; 2; 2; 2];
            timeVector = [0.01; 0.01; 0.01; 0.01];
            threshold = 0.003;

            metrics = compute_breakdown_metrics(pdSignal, voltageSignal, timeVector, threshold);

            testCase.verifyEqual(metrics.AcquisitionDurationSec, 4, 'AbsTol', 1e-12);
            testCase.verifyEqual(metrics.DischargeRate_Hz, 0.5, 'AbsTol', 1e-12);
            testCase.verifyEqual(metrics.MaxDischargeAbs_V, 0.005, 'AbsTol', 1e-12);
        end
    end
end
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
matlab -batch "addpath(pwd); results = runtests('tests/test_breakdown_metrics.m'); assert(~all([results.Passed]))"
```

Expected: MATLAB reports failure because `compute_breakdown_metrics` is undefined.

- [ ] **Step 3: Implement the minimal function**

Create `compute_breakdown_metrics.m`:

```matlab
function metrics = compute_breakdown_metrics(pdSignal, voltageSignal, timeVector, threshold)
%COMPUTE_BREAKDOWN_METRICS Calculate one acquisition's breakdown trend metrics.

pdSignal = double(pdSignal(:));
voltageSignal = double(voltageSignal(:));
timeVector = double(timeVector(:));

durationSec = acquisitionDuration(timeVector);
validMask = abs(pdSignal) > threshold;
validPulseCount = sum(validMask);

if validPulseCount > 0
    maxDischargeAbsV = max(abs(pdSignal(validMask)));
else
    maxDischargeAbsV = 0;
end

metrics = struct();
metrics.CH3_RMS = sqrt(mean(voltageSignal .^ 2));
metrics.ValidPulseCount = validPulseCount;
metrics.AcquisitionDurationSec = durationSec;
metrics.DischargeRate_Hz = validPulseCount / durationSec;
metrics.MaxDischargeAbs_V = maxDischargeAbsV;
end

function durationSec = acquisitionDuration(timeVector)
if numel(timeVector) >= 2
    durationSec = timeVector(end) - timeVector(1);
else
    durationSec = 0;
end

if durationSec <= 0 || ~isfinite(durationSec)
    diffs = diff(timeVector);
    diffs = diffs(isfinite(diffs) & diffs > 0);
    if ~isempty(diffs)
        durationSec = numel(timeVector) * median(diffs);
    else
        durationSec = max(numel(timeVector), 1);
    end
end
end
```

- [ ] **Step 4: Run the test to verify it passes**

Run:

```powershell
matlab -batch "addpath(pwd); results = runtests('tests/test_breakdown_metrics.m'); assert(all([results.Passed]))"
```

Expected: all three tests pass.

- [ ] **Step 5: Commit Task 1**

Run:

```powershell
git add compute_breakdown_metrics.m tests/test_breakdown_metrics.m
git commit -m "feat: add breakdown metric calculation"
```

---

### Task 2: Monitor Skeleton and Configuration

**Files:**
- Create: `tek_breakdown_monitor.m`

- [ ] **Step 1: Write a syntax-focused monitor skeleton**

Create `tek_breakdown_monitor.m` with the public function, input parser, output directory creation, session info writing, and cleanup. Do not add waveform acquisition yet.

```matlab
function tek_breakdown_monitor(varargin)
%TEK_BREAKDOWN_MONITOR Monitor breakdown trend metrics from Tektronix data.

cfg = parseInputs(varargin{:});
startStamp = datestr(now, 'yyyymmdd_HHMMSS');

if ~exist(cfg.ResultRoot, 'dir')
    mkdir(cfg.ResultRoot);
end
outputDir = fullfile(cfg.ResultRoot, ['Breakdown_' startStamp]);
if ~exist(outputDir, 'dir')
    mkdir(outputDir);
end

fprintf('Breakdown output directory: %s\n', outputDir);
fprintf('Scope address: %s\n', cfg.Addr);
fprintf('Acquisition points: %d\n', cfg.AcquisitionPoints);
fprintf('Discharge threshold: %.6g V\n', cfg.ProcessingThreshold);

scope = [];
cleanupObj = onCleanup(@cleanup); %#ok<NASGU>
saveSessionInfo(outputDir, '', cfg);

    function cleanup()
        if ~isempty(scope)
            try
                clear scope
            catch
            end
        end
        fprintf('Breakdown session ended.\n');
    end
end

function cfg = parseInputs(varargin)
p = inputParser;
p.addParameter('Addr', 'USB0::0x0699::0x0401::B010503::INSTR', @ischar);
p.addParameter('ResultRoot', fullfile(pwd, 'breakdown_results'), @ischar);
p.addParameter('DurationMinutes', 0, @(x) isnumeric(x) && isscalar(x) && x >= 0);
p.addParameter('PlotIntervalSec', 5, @(x) isnumeric(x) && isscalar(x) && x > 0);
p.addParameter('PollIntervalSec', 0.2, @(x) isnumeric(x) && isscalar(x) && x >= 0);
p.addParameter('TimeoutSec', 60, @(x) isnumeric(x) && isscalar(x) && x >= 10);
p.addParameter('AcquisitionPoints', 1e6, @(x) isnumeric(x) && isscalar(x) && x > 0);
p.addParameter('ProcessingThreshold', 0.003, @(x) isnumeric(x) && isscalar(x) && x >= 0);
p.addParameter('InputBufferSize', 10000000, @(x) isnumeric(x) && isscalar(x) && x > 0);
p.addParameter('FigureVisible', true, @(x) islogical(x) || isnumeric(x));
p.parse(varargin{:});

cfg = p.Results;
cfg.AcquisitionPoints = round(cfg.AcquisitionPoints);
cfg.FigureVisible = logical(cfg.FigureVisible);
end

function saveSessionInfo(outputDir, deviceId, cfg)
fid = fopen(fullfile(outputDir, 'session_info.txt'), 'w');
if fid < 0
    return;
end
cleaner = onCleanup(@() fclose(fid)); %#ok<NASGU>
fprintf(fid, 'StartTime: %s\n', datestr(now, 'yyyy-mm-dd HH:MM:SS'));
fprintf(fid, 'ScopeIDN: %s\n', deviceId);
fprintf(fid, 'Purpose: Breakdown trend monitor\n\n');
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
```

- [ ] **Step 2: Run syntax checks**

Run:

```powershell
matlab -batch "checkcode('tek_breakdown_monitor.m'); which tek_breakdown_monitor"
```

Expected: MATLAB finds `tek_breakdown_monitor` and reports no blocking syntax errors.

- [ ] **Step 3: Commit Task 2**

Run:

```powershell
git add tek_breakdown_monitor.m
git commit -m "feat: add breakdown monitor skeleton"
```

---

### Task 3: Acquisition Loop and Continuous Logging

**Files:**
- Modify: `tek_breakdown_monitor.m`

- [ ] **Step 1: Add hardware acquisition helpers**

In `tek_breakdown_monitor.m`, add helpers equivalent to the proven PRPD monitor behavior:

```matlab
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

function [pdSignal, voltageSignal, timeVector] = fetchBreakdownSignals(scope, cfg)
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

function setBufferIfAvailable(scope, bufferSize)
if isprop(scope, 'InputBufferSize')
    scope.InputBufferSize = bufferSize;
end
if isprop(scope, 'OutputBufferSize')
    scope.OutputBufferSize = bufferSize;
end
end
```

- [ ] **Step 2: Add log initialization and flushing helpers**

Add:

```matlab
function logStruct = initBreakdownLog()
logStruct = struct();
logStruct.AcquisitionIndex = zeros(0, 1);
logStruct.ElapsedSec = zeros(0, 1);
logStruct.Timestamp = cell(0, 1);
logStruct.CH3_RMS = zeros(0, 1);
logStruct.DischargeRate_Hz = zeros(0, 1);
logStruct.MaxDischargeAbs_V = zeros(0, 1);
logStruct.ValidPulseCount = zeros(0, 1);
logStruct.AcquisitionDurationSec = zeros(0, 1);
end

function logStruct = appendBreakdownLog(logStruct, acquisitionIndex, elapsedSec, metrics)
logStruct.AcquisitionIndex(end + 1, 1) = acquisitionIndex;
logStruct.ElapsedSec(end + 1, 1) = elapsedSec;
logStruct.Timestamp{end + 1, 1} = datestr(now, 'yyyy-mm-dd HH:MM:SS.FFF');
logStruct.CH3_RMS(end + 1, 1) = metrics.CH3_RMS;
logStruct.DischargeRate_Hz(end + 1, 1) = metrics.DischargeRate_Hz;
logStruct.MaxDischargeAbs_V(end + 1, 1) = metrics.MaxDischargeAbs_V;
logStruct.ValidPulseCount(end + 1, 1) = metrics.ValidPulseCount;
logStruct.AcquisitionDurationSec(end + 1, 1) = metrics.AcquisitionDurationSec;
end

function T = breakdownLogToTable(logStruct)
T = table( ...
    logStruct.AcquisitionIndex, ...
    logStruct.ElapsedSec, ...
    logStruct.Timestamp, ...
    logStruct.CH3_RMS, ...
    logStruct.DischargeRate_Hz, ...
    logStruct.MaxDischargeAbs_V, ...
    logStruct.ValidPulseCount, ...
    logStruct.AcquisitionDurationSec, ...
    'VariableNames', { ...
        'AcquisitionIndex', ...
        'ElapsedSec', ...
        'Timestamp', ...
        'CH3_RMS', ...
        'DischargeRate_Hz', ...
        'MaxDischargeAbs_V', ...
        'ValidPulseCount', ...
        'AcquisitionDurationSec'});
end

function flushBreakdownLog(outputDir, logStruct, cfg)
T = breakdownLogToTable(logStruct); %#ok<NASGU>
save(fullfile(outputDir, 'breakdown_log.mat'), 'T', 'cfg');
try
    writetable(T, fullfile(outputDir, 'breakdown_log.xlsx'));
catch ME
    fprintf(2, 'Breakdown XLSX log write failed: %s\n', ME.message);
end
end
```

- [ ] **Step 3: Wire the main acquisition loop**

Replace the skeleton body after `saveSessionInfo` with:

```matlab
logStruct = initBreakdownLog();
acqCount = 0;
sessionStart = tic;

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

    writeline(scope, 'ACQuire:STOPAfter SEQuence');
    pause(0.1);
    writeline(scope, 'ACQuire:STATE RUN');
    pause(0.1);

    fprintf('Acquiring breakdown trend data. Press Ctrl+C to stop.\n');
    while cfg.DurationMinutes <= 0 || toc(sessionStart) < cfg.DurationMinutes * 60
        waitForSequenceComplete(scope, cfg);
        [pdSignal, voltageSignal, timeVector] = fetchBreakdownSignals(scope, cfg);
        acqCount = acqCount + 1;

        elapsedSec = toc(sessionStart);
        metrics = compute_breakdown_metrics(pdSignal, voltageSignal, timeVector, cfg.ProcessingThreshold);
        logStruct = appendBreakdownLog(logStruct, acqCount, elapsedSec, metrics);
        flushBreakdownLog(outputDir, logStruct, cfg);

        fprintf('Acq %d: elapsed=%.1f s, CH3 RMS=%.6g, rate=%.6g Hz, max PD=%.6g V\n', ...
            acqCount, elapsedSec, metrics.CH3_RMS, metrics.DischargeRate_Hz, metrics.MaxDischargeAbs_V);

        writeline(scope, 'ACQuire:STATE RUN');
        pause(cfg.PollIntervalSec);
    end
catch ME
    fprintf(2, 'Error: %s\n', ME.message);
    if acqCount > 0
        flushBreakdownLog(outputDir, logStruct, cfg);
    end
end
```

- [ ] **Step 4: Run syntax checks**

Run:

```powershell
matlab -batch "addpath(pwd); checkcode('tek_breakdown_monitor.m'); which compute_breakdown_metrics; which tek_breakdown_monitor"
```

Expected: MATLAB finds both functions and reports no blocking syntax errors.

- [ ] **Step 5: Commit Task 3**

Run:

```powershell
git add tek_breakdown_monitor.m
git commit -m "feat: add breakdown acquisition logging"
```

---

### Task 4: Live Plot and Figure Saving

**Files:**
- Modify: `tek_breakdown_monitor.m`

- [ ] **Step 1: Add figure creation and plot updating helpers**

Add:

```matlab
function fig = createBreakdownFigure(cfg)
if cfg.FigureVisible
    visible = 'on';
else
    visible = 'off';
end
fig = figure('Name', 'Breakdown Trend Monitor', ...
    'Color', 'w', ...
    'Visible', visible, ...
    'Position', [100, 120, 900, 560]);
end

function updateBreakdownPlot(fig, logStruct)
figure(fig);
clf(fig);
T = breakdownLogToTable(logStruct);

if isempty(T.ElapsedSec)
    return;
end

yyaxis left;
plot(T.ElapsedSec, T.CH3_RMS, 'b-o', 'LineWidth', 1.2, 'MarkerSize', 4);
ylabel('CH3 RMS (V)');

yyaxis right;
plot(T.ElapsedSec, T.DischargeRate_Hz, 'r-s', 'LineWidth', 1.2, 'MarkerSize', 4);
hold on;
plot(T.ElapsedSec, T.MaxDischargeAbs_V, 'k-^', 'LineWidth', 1.2, 'MarkerSize', 4);
ylabel('Discharge rate (Hz) / Max abs PD (V)');

xlabel('Elapsed time (s)');
title('Breakdown Trend');
legend({'CH3 RMS', 'Discharge rate', 'Max abs PD'}, 'Location', 'best');
grid on;
box on;
drawnow;
end
```

- [ ] **Step 2: Wire plotting into the acquisition loop**

Before the `try` block, add:

```matlab
fig = createBreakdownFigure(cfg);
lastPlotSave = tic;
```

After `appendBreakdownLog`, call:

```matlab
updateBreakdownPlot(fig, logStruct);
if toc(lastPlotSave) >= cfg.PlotIntervalSec
    saveas(fig, fullfile(outputDir, 'breakdown_trend.png'));
    lastPlotSave = tic;
end
```

In the `catch` block after log flushing, add:

```matlab
if exist('fig', 'var') && isvalid(fig) && acqCount > 0
    saveas(fig, fullfile(outputDir, 'breakdown_trend.png'));
end
```

- [ ] **Step 3: Run syntax checks**

Run:

```powershell
matlab -batch "addpath(pwd); checkcode('tek_breakdown_monitor.m')"
```

Expected: no blocking syntax errors.

- [ ] **Step 4: Commit Task 4**

Run:

```powershell
git add tek_breakdown_monitor.m
git commit -m "feat: add breakdown trend plotting"
```

---

### Task 5: User Entry Script

**Files:**
- Create: `main_breakdown_logger.m`

- [ ] **Step 1: Create the user-facing script**

Create `main_breakdown_logger.m`:

```matlab
%% main_breakdown_logger
% Real-time breakdown trend logger for the inter-turn bubble discharge experiment.
%
% Scope channel convention:
%   CH2 = HFCT high-frequency current / PD signal.
%   CH3 = synchronized power-frequency voltage signal.
%
% The trend plot starts when this script runs and refreshes after each
% completed oscilloscope acquisition.

clear;
clc;

%% Experiment information
experimentName = 'interturn_bubble_breakdown';
scopeAddr = 'USB0::0x0699::0x0401::B010503::INSTR';

resultRoot = fullfile(pwd, 'breakdown_results');

%% Logger timing
% DurationMinutes = 0 means keep running until Ctrl+C.
durationMinutes = 0;

% Save the latest figure at this interval. Data is still flushed every acquisition.
plotIntervalSec = 5;

% Poll interval only controls how often MATLAB checks for new waveform data.
pollIntervalSec = 0.2;

%% Readout size
acquisitionPoints = 1e6;

%% Discharge threshold
% Same role as ProcessingThreshold in tek_prpd_monitor.m.
processingThreshold = 0.003;

% true shows the live trend figure; false still saves data in background.
figureVisible = true;

%% Start logger
fprintf('Experiment: %s\n', experimentName);
fprintf('Trend metrics: CH3 RMS, discharge frequency, max discharge amplitude.\n');

tek_breakdown_monitor( ...
    'Addr', scopeAddr, ...
    'ResultRoot', resultRoot, ...
    'DurationMinutes', durationMinutes, ...
    'PlotIntervalSec', plotIntervalSec, ...
    'PollIntervalSec', pollIntervalSec, ...
    'AcquisitionPoints', acquisitionPoints, ...
    'ProcessingThreshold', processingThreshold, ...
    'FigureVisible', figureVisible);
```

- [ ] **Step 2: Run syntax checks**

Run:

```powershell
matlab -batch "addpath(pwd); checkcode('main_breakdown_logger.m'); checkcode('tek_breakdown_monitor.m')"
```

Expected: no blocking syntax errors.

- [ ] **Step 3: Commit Task 5**

Run:

```powershell
git add main_breakdown_logger.m
git commit -m "feat: add breakdown logger entry point"
```

---

### Task 6: Final Verification and Push

**Files:**
- Review all new files.

- [ ] **Step 1: Run metric tests**

Run:

```powershell
matlab -batch "addpath(pwd); results = runtests('tests/test_breakdown_metrics.m'); assert(all([results.Passed]))"
```

Expected: all metric tests pass.

- [ ] **Step 2: Run MATLAB static checks**

Run:

```powershell
matlab -batch "checkcode('compute_breakdown_metrics.m'); checkcode('tek_breakdown_monitor.m'); checkcode('main_breakdown_logger.m')"
```

Expected: no blocking syntax errors. Non-blocking style warnings may be reviewed and either fixed if scoped to new code or reported.

- [ ] **Step 3: Review Git status**

Run:

```powershell
git status --short
```

Expected: only pre-existing unrelated local changes remain, or the worktree is clean except for files intentionally left by the user.

- [ ] **Step 4: Push commits**

Run:

```powershell
git push
```

Expected: new implementation commits are pushed to `origin/main`.

---

## Hardware Verification Checklist

Run this only when the oscilloscope is connected and the experiment setup is ready.

- [ ] Start MATLAB in `D:\桌面\匝间气泡放电实验`.
- [ ] Run `main_breakdown_logger`.
- [ ] Confirm the oscilloscope responds to `*IDN?`.
- [ ] Confirm the live figure appears immediately.
- [ ] Confirm each acquisition adds one point to all three curves.
- [ ] Confirm `breakdown_results/Breakdown_<timestamp>` contains:
  - `session_info.txt`
  - `breakdown_log.mat`
  - `breakdown_log.xlsx`
  - `breakdown_trend.png`
- [ ] Stop with Ctrl+C and confirm completed acquisition data remains saved.
