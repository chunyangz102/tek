function [crossing_points, crossing_directions] = ZeroCrossingFinder(time_data, signal_data, threshold_value)
% Zero-crossing detection based on sign changes and Lagrange linear interpolation to find intersections with the threshold.
% Points that just graze zero might not be detected.
% Can be extended to find intersections between two datasets.
% Input:
% 1. time_data: x-coordinates (time) of the data to detect.
% 2. signal_data: y-coordinates (signal) of the data to detect.
% 3. threshold_value: a scalar threshold.
% Output:
% 1. crossing_points: list of intersection points (times).
% 2. crossing_directions: crossing direction, +1 for positive crossing (downward), -1 for negative crossing (upward), 0 for constant threshold point.

cutoff_freq = 2000;  % Default cutoff frequency for low-pass filter (0 means no filtering)

% Apply low-pass filter if cutoff_freq > 0
if cutoff_freq > 0
    fs = 1 / (time_data(2) - time_data(1));  % Calculate sampling frequency
    [b, a] = butter(4, cutoff_freq / (fs / 2), 'low');  % 4th-order Butterworth low-pass filter
    signal_data = filtfilt(b, a, signal_data);  % Apply zero-phase filtering to avoid phase distortion
end

crossing_points = []; crossing_directions = [];
normalized_signal = signal_data - threshold_value; % Normalize to zero
sign_prev = sign(normalized_signal(1 : end - 1));
sign_next = sign(normalized_signal(2 : end));
sign_product = sign_prev .* sign_next;
% Find indices where sign_product is negative or zero
suspect_indices = find(sign_product <= 0);
for i = 1 : length(suspect_indices)
  idx = suspect_indices(i);
  time_left = time_data(idx); time_right = time_data(idx + 1);
  signal_left = normalized_signal(idx); signal_right = normalized_signal(idx + 1);
  if signal_left == 0
    crossing_time = time_left;
  elseif signal_right == 0
    crossing_time = time_right;
  else
    crossing_time = (-time_left * signal_right + time_right * signal_left) / (signal_left - signal_right);
  end
  crossing_points = [crossing_points, crossing_time];
  crossing_directions = [crossing_directions, sign_prev(idx)];
end
end