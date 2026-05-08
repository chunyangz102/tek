%% main_prpd_logger
% Long-term PRPD logger for the inter-turn bubble discharge experiment.
%
% This entry point uses the same acquisition/processing logic as the code
% folder:
%   1. The scope is put into ACQuire:STOPAfter SEQuence mode.
%   2. MATLAB waits until each sequence completes.
%   3. CH3 voltage and CH2 HFCT/PD are read by binary CURVE? transfer.
%   4. PRPD points are calculated and saved periodically.
%
% Scope channel convention:
%   CH2 = HFCT high-frequency current / PD signal.
%   CH3 = synchronized power-frequency voltage signal.

clear;
clc;

%% Experiment information
experimentName = 'interturn_bubble_discharge';
scopeAddr = 'USB0::0x0699::0x0401::B010503::INSTR';

resultRoot = fullfile(pwd, 'PRPD_results');

%% Logger timing
% DurationMinutes = 0 means keep running until Ctrl+C.
durationMinutes = 0;

% Save one PRPD figure and one MAT data file every minute.
plotIntervalSec = 10;

% Poll interval only controls how often MATLAB checks for new waveform data.
pollIntervalSec = 0.2;

%% Readout size
% code/SignalAcquisition.m uses 1e6 points.
acquisitionPoints = 1e6;

%% PRPD processing
powerFreqHz = 50;
signalLabel = '高频';

% Add this if CH3 zero-crossing phase needs calibration.
phaseOffsetDeg = 0;

% Same role as processing_threshold in code/SignalProcessor.m.
processingThreshold = 0.02;

% true shows the live PRPD figure; false still saves figures in background.
figureVisible = true;

%% Start logger
fprintf('Experiment: %s\n', experimentName);
fprintf('Using code-folder acquisition logic: STOPAfter SEQuence + binary CURVE? readout.\n');

tek_prpd_monitor( ...
    'Addr', scopeAddr, ...
    'ResultRoot', resultRoot, ...
    'DurationMinutes', durationMinutes, ...
    'PlotIntervalSec', plotIntervalSec, ...
    'PollIntervalSec', pollIntervalSec, ...
    'AcquisitionPoints', acquisitionPoints, ...
    'SignalLabel', signalLabel, ...
    'PowerFreqHz', powerFreqHz, ...
    'PhaseOffsetDeg', phaseOffsetDeg, ...
    'ProcessingThreshold', processingThreshold, ...
    'FigureVisible', figureVisible);
