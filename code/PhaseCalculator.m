function [pd_amplitudes_voltage, phases_voltage, pd_amplitudes_accel, phases_accel] = PhaseCalculator(pd_signal, voltage_signal, acceleration_signal, time_vector, amplitude_threshold, voltage_frequency, acceleration_frequency, cutoff_freq_voltage, cutoff_freq_accel, hyst_factor_voltage, hyst_factor_accel)
    % Merged module: Low-pass filter, zero-crossing finder with optional hysteresis, phase/amplitude calculator
    % Inputs: signals, time, threshold, frequencies; optional cutoffs (default 2000 Hz, 0 = no filter)
    % Optional: hyst_factor_voltage and hyst_factor_accel (default 0.05, 0 = no hysteresis, uses sign change)
    if nargin < 8, cutoff_freq_voltage = 1000; end
    if nargin < 9, cutoff_freq_accel = 1000; end
    if nargin < 10, hyst_factor_voltage = 0.05; end
    if nargin < 11, hyst_factor_accel = 0.05; end
    
    % Helper: Zero-crossing finder (integrated, with hysteresis support)
    function [crossing_times, crossing_dirs] = findZeroCrossings(time_data, signal_data, threshold_value, cutoff_freq, hyst_factor)
        crossing_times = []; crossing_dirs = [];
        if cutoff_freq > 0
            fs = 1 / (time_data(2) - time_data(1));  
            [b, a] = butter(4, cutoff_freq / (fs / 2), 'low');  
            signal_data = filtfilt(b, a, signal_data);  
        end
        normalized_signal = signal_data - threshold_value;  % Normalize to zero mean
        
        if hyst_factor > 0
            % Hysteresis mode: compute thresholds based on signal RMS
            rms_val = rms(normalized_signal);
            pos_thresh = hyst_factor * rms_val;
            neg_thresh = -pos_thresh;
            
            % Initialize state
            state = 'mid';
            if normalized_signal(1) > pos_thresh
                state = 'high';
            elseif normalized_signal(1) < neg_thresh
                state = 'low';
            end
            
            for i = 2:length(normalized_signal)
                prev = normalized_signal(i-1);
                curr = normalized_signal(i);
                time_prev = time_data(i-1);
                time_curr = time_data(i);
                
                if strcmp(state, 'high')
                    if curr < neg_thresh
                        % Downward crossing (negative direction, 180°)
                        crossing_time = time_prev + (0 - prev) / (curr - prev) * (time_curr - time_prev);
                        crossing_times = [crossing_times, crossing_time];
                        crossing_dirs = [crossing_dirs, 1];  % sign_prev positive
                        state = 'low';
                    end
                elseif strcmp(state, 'low')
                    if curr > pos_thresh
                        % Upward crossing (positive direction, 0°)
                        crossing_time = time_prev + (0 - prev) / (curr - prev) * (time_curr - time_prev);
                        crossing_times = [crossing_times, crossing_time];
                        crossing_dirs = [crossing_dirs, -1];  % sign_prev negative
                        state = 'high';
                    end
                elseif strcmp(state, 'mid')
                    if curr > pos_thresh
                        state = 'high';
                    elseif curr < neg_thresh
                        state = 'low';
                    end
                end
            end
        else
            % Original sign-change mode (no hysteresis)
            sign_prev = sign(normalized_signal(1:end-1));
            sign_next = sign(normalized_signal(2:end));
            sign_product = sign_prev .* sign_next;
            suspect_indices = find(sign_product <= 0);
            for i = 1:length(suspect_indices)
                idx = suspect_indices(i);
                time_left = time_data(idx); time_right = time_data(idx + 1);
                signal_left = normalized_signal(idx); signal_right = normalized_signal(idx + 1);
                if signal_left == 0
                    crossing_time = time_left;
                elseif signal_right == 0
                    crossing_time = time_right;
                else
                    crossing_time = time_left + (0 - signal_left) / (signal_right - signal_left) * (time_right - time_left);
                end
                crossing_times = [crossing_times, crossing_time];
                crossing_dirs = [crossing_dirs, sign_prev(idx)];
            end
        end
    end

    % Valid PD indices
    valid_pd_indices = find(abs(pd_signal) > amplitude_threshold);
    valid_pd_count = length(valid_pd_indices);
    if valid_pd_count == 0
        pd_amplitudes_voltage = []; phases_voltage = [];
        pd_amplitudes_accel = []; phases_accel = [];
        return;
    end
    pd_times = time_vector(valid_pd_indices);

    % For voltage: Use first positive (upward) crossing as phase 0° reference
    phases_voltage = [];
    pd_amplitudes_voltage = [];
    if ~isempty(voltage_signal)
        [crossing_times_voltage, crossing_dirs_voltage] = findZeroCrossings(time_vector, voltage_signal, mean(voltage_signal), cutoff_freq_voltage, hyst_factor_voltage);
        positive_crossing_indices_voltage = find(crossing_dirs_voltage == -1);  % Upward crossings (0°)
        phases_voltage = zeros(1, valid_pd_count);
        pd_amplitudes_voltage = pd_signal(valid_pd_indices)';
        if ~isempty(positive_crossing_indices_voltage)
            first_pos_cross_time = crossing_times_voltage(positive_crossing_indices_voltage(1));
            phase_indices_voltage = mod((pd_times - first_pos_cross_time) / (1 / voltage_frequency) * 360, 360);
            phases_voltage = phase_indices_voltage;
        end
    end
    % For acceleration: Similar, use first positive crossing as phase 0°
    phases_accel = [];
    pd_amplitudes_accel = [];
    if ~isempty(acceleration_signal)
        [crossing_times_accel, crossing_dirs_accel] = findZeroCrossings(time_vector, acceleration_signal, mean(acceleration_signal), cutoff_freq_accel, hyst_factor_accel);
        positive_crossing_indices_accel = find(crossing_dirs_accel == -1);  % Upward crossings (0°)
        phases_accel = zeros(1, valid_pd_count);
        pd_amplitudes_accel = pd_signal(valid_pd_indices)';
        if ~isempty(positive_crossing_indices_accel)
            first_pos_cross_time = crossing_times_accel(positive_crossing_indices_accel(1));
            phase_indices_accel = mod((pd_times - first_pos_cross_time) / (1 / acceleration_frequency) * 360, 360);
            phases_accel = phase_indices_accel;
        end
    end
end

