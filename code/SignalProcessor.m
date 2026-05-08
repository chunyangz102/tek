close all; clear; clc;
% Parameters
processing_threshold = 0.002;  % Amplitude threshold V
voltage_frequency = 50;  % Hz
acceleration_frequency = 100;  % Hz
calibration_factor = 12500;  % pC/V
cutoff_freq_voltage = 2000;  % Hz, set 0 to disable
cutoff_freq_accel = 2000;

% Data selection: mat_files cell array, slice_fraction string (e.g., 'first1/5', 'last1/2', '' for full)
mat_files = {'complete420t3d20t5min.mat'};  % Example: {'file1.mat', 'file2.mat'}
slice_fraction = '';  % Per-file slice

% Initialize accumulators
phases_accum_voltage = []; pd_amplitudes_accum_voltage = [];                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                
phases_accum_accel = []; pd_amplitudes_accum_accel = [];
pd_repetition_rates = []; pd_mean_values = []; times = [];
cumulative_duration = 0;
total_segment_time = 0;  % Sum of all acquisition durations

%% Main processing: Loop files sequentially
for m = 1:length(mat_files)
    load(mat_files{m}, 'data_storage');
    % num_cols = size(data_storage,2);
    has_voltage = true;
    has_accel = false;
    
    non_empty_indices = find(~cellfun(@isempty, data_storage(:,1)));
    time_offset = cumulative_duration;  % Offset for continuous time across files
    for k = non_empty_indices'
        pd_signal = data_storage{k,1};
        if has_voltage && has_accel
            voltage_signal = data_storage{k,2};
            acceleration_signal = data_storage{k,3};
            time_vector = data_storage{k,4};
            elapsed = data_storage{k,5};
            rms_voltage = data_storage{k,6};
            max_pd = data_storage{k,7};
        elseif has_voltage
            voltage_signal = data_storage{k,2};
            acceleration_signal = [];
            time_vector = data_storage{k,4};
            elapsed = data_storage{k,5};
            rms_voltage = data_storage{k,6};
            max_pd = data_storage{k,7};
        elseif has_accel
            voltage_signal = [];
            acceleration_signal = data_storage{k,2};
            time_vector = data_storage{k,4};
            elapsed = data_storage{k,5};
            rms_accel = data_storage{k,6};
            max_pd = data_storage{k,7};
        else
            continue;  % Skip if no signals
        end
        
        % Apply slice if specified (single file only for simplicity)
        if isscalar(mat_files) && ~isempty(slice_fraction)
            len = length(time_vector);
            if startsWith(slice_fraction, 'first')
                frac_str = extractAfter(slice_fraction, 'first');
                frac_parts = str2double(split(frac_str, '/'));
                if length(frac_parts) == 2, frac = frac_parts(1)/frac_parts(2); crop_idx = 1:round(len * frac); end
            elseif startsWith(slice_fraction, 'last')
                frac_str = extractAfter(slice_fraction, 'last');
                frac_parts = str2double(split(frac_str, '/'));
                if length(frac_parts) == 2, frac = frac_parts(1)/frac_parts(2); crop_idx = round(len * (1 - frac)) + 1:len; end
            else
                crop_idx = 1:len;
            end
            pd_signal = pd_signal(crop_idx); 
            if has_voltage
                voltage_signal = voltage_signal(crop_idx);
            end
            if has_accel
                acceleration_signal = acceleration_signal(crop_idx);
            end
            time_vector = time_vector(crop_idx);
        end
        
        acquisition_duration = time_vector(end) - time_vector(1);
        total_segment_time = total_segment_time + acquisition_duration;
        
        % Compute phases/amplitudes
        [pd_amps_vol, phases_vol, pd_amps_accel, phases_accel] = PhaseCalculator(...
            pd_signal, voltage_signal, acceleration_signal, time_vector, processing_threshold, ...
            voltage_frequency, acceleration_frequency, cutoff_freq_voltage, cutoff_freq_accel);
        if has_voltage
            phases_accum_voltage = [phases_accum_voltage, phases_vol(:)'];
            pd_amplitudes_accum_voltage = [pd_amplitudes_accum_voltage, (pd_amps_vol * calibration_factor)'];
        end
        if has_accel
            phases_accum_accel = [phases_accum_accel, phases_accel(:)'];
            pd_amplitudes_accum_accel = [pd_amplitudes_accum_accel, (pd_amps_accel * calibration_factor)'];
        end
        
        % Stats
        valid_pd_count = length(pd_amps_vol);
        repetition_rate = valid_pd_count / acquisition_duration;
        if ~isempty(pd_amps_accel) && ~any(isnan(pd_amps_vol))
            mean_value = mean(abs(pd_amps_accel) * calibration_factor);
        else
            mean_value = 0;  % Handle empty or NaN cases
        end
        pd_repetition_rates = [pd_repetition_rates repetition_rate];
        pd_mean_values = [pd_mean_values mean_value];
        if has_accel & has_voltage
            elapsed = data_storage{k,5};  % Use actual elapsed time
        else
            elapsed = data_storage{k,5};  % Use actual elapsed time
        end
        times = [times time_offset + elapsed];  % Add offset for continuous time
    end
    
    % Update cumulative (use last elapsed if available)
    if ~isempty(non_empty_indices)
        if has_accel & has_voltage
            file_duration = max([data_storage{non_empty_indices,5}]);  % Use max elapsed as file period (e.g., 10min)
        else
            file_duration = max([data_storage{non_empty_indices,4}]);  % Use max elapsed as file period (e.g., 10min)
        end
    else
        file_duration = 0;
    end
    cumulative_duration = cumulative_duration + file_duration;
    clear data_storage;  % Release memory
end

% Debugging output to verify scaling
disp(['Total points (accel): ', num2str(length(phases_accum_accel))]);
disp(['Total segment time (s): ', num2str(total_segment_time)]);

% Plot evolution curves
% Data smoothing, use if necessary
pd_mean_values_smoothed = smoothdata(pd_mean_values, 'gaussian', 5);
pd_repetition_rates_smoothed = smoothdata(pd_repetition_rates, 'gaussian', 5);
if ~isempty(times)
    figure(3); set(gcf, 'Position', [500,200,720,480]);
    yyaxis left; plot(times, pd_mean_values_smoothed / 1e3, 'b-', 'LineWidth', 1.5); ytickformat('%.2f'); ylabel('$Q_{\mathrm{c}} \; (\mathrm{nC})$','Interpreter','latex');
    yyaxis right; plot(times, pd_repetition_rates_smoothed / 1e3, 'r-', 'LineWidth', 1.5); ylabel('$N_{\mathrm{r}} \; (\times 10^{3} \cdot \mathrm{s}^{-1})$','Interpreter','latex');
    xlim([0 max(times)]); xlabel('$t \; (\mathrm{s})$','Interpreter','latex'); title('PD Evolution Over Time'); grid on;
else
    disp('No data for evolution plot.');
end

% Plot V-PRPD (if has_voltage)
if has_voltage
    total_points_vol = length(phases_accum_voltage);
    if total_points_vol > 0 && total_segment_time > 0
        figure(1); set(gcf, 'Position', [100,200,720,480]);
        phases_vol_col = phases_accum_voltage(:);
        amps_vol_col = abs(pd_amplitudes_accum_voltage(:));
        pts_vol = [phases_vol_col, amps_vol_col];
        [f_vol] = ksdensity(pts_vol, pts_vol);  % Use input points as query points for per-point density
        f_vol(isnan(f_vol)) = 0;  % Handle NaN
        f_vol(f_vol < 0) = 0;  % Clamp negative values
        norm_color_vol = f_vol * range(phases_vol_col) * range(amps_vol_col) * total_points_vol / total_segment_time;  % Convert to repetition rate)
        max_freq_vol = max(norm_color_vol);  % Maximum frequency for colorbar
        if max_freq_vol == 0
            norm_color_vol = ones(size(f_vol)) * 0.5;  % Fallback to neutral color if zero density
        end
        scatter(phases_vol_col, amps_vol_col/1e3, 10, norm_color_vol, 'filled');
        colormap('parula'); 
        c = colorbar; ylabel(c, '$N_{\mathrm{r}} \; (\times 10^{3} \cdot \mathrm{s}^{-1})$','Interpreter','latex');
        clim([0 max(max_freq_vol,calibration_factor)]);c.TickLabels = [];
        c.Ticks = linspace(0, max(max_freq_vol,calibration_factor), 11);
        % c.TickLabels = arrayfun(@(x) sprintf('%.2f', x), c.Ticks, 'UniformOutput', false);
        xlabel('$\varphi_{\mathrm{v}} \; (^{\circ })$','Interpreter','latex'); ylabel('$Q_{\mathrm{c}} \; (\mathrm{nC})$','Interpreter','latex'); ytickformat('%.2f'); title('PRPD Pattern versus Applied Voltage');
        axis([0 360 0 max(amps_vol_col/1e3)*1.2]); xticks(0:45:360); grid on;
        
        yyaxis right; phase_vals = 0:0.01:360; sine_vol = sin(phase_vals * 2*pi/360);
        plot(phase_vals, sine_vol, 'k--', 'LineWidth', 1.5); ylabel('$U_{\mathrm{ref}}$','Interpreter','latex');
        ax_right = gca; ax_right.YTickLabel = {}; ax_right.YTickLabelMode = 'manual';
    else
        disp('No PD detected for V-PD.');
    end
end

% Plot A-PRPD (if has_accel)
if has_accel
    total_points_accel = length(phases_accum_accel);
    if total_points_accel > 0 && total_segment_time > 0
        figure(2); set(gcf, 'Position', [900,200,720,480]);
        phases_accel_col = phases_accum_accel(:);
        amps_accel_col = abs(pd_amplitudes_accum_accel(:));
        pts_accel = [phases_accel_col, amps_accel_col];
        [f_accel] = ksdensity(pts_accel, pts_accel);  % Use input points as query points
        f_accel(isnan(f_accel)) = 0;  % Handle NaN
        f_accel(f_accel < 0) = 0;  % Clamp negative values
        norm_color_accel = f_accel * range(phases_accel_col) * range(amps_accel_col) * total_points_accel / total_segment_time;  % Convert to repetition rate
        max_freq_accel = max(norm_color_accel);  % Maximum frequency for colorbar
        if max_freq_accel == 0
            norm_color_accel = ones(size(f_accel)) * 0.5;  % Fallback to neutral color
        end
        scatter(phases_accel_col, amps_accel_col/1e3, 10, norm_color_accel, 'filled');
        colormap('parula'); 
        c = colorbar; ylabel(c, '$N_{\mathrm{r}} \; (\times 10^{3} \cdot \mathrm{s}^{-1})$','Interpreter','latex');
        clim([0 max(max_freq_accel,calibration_factor)]);c.TickLabels = [];
        c.Ticks = linspace(0, max(max_freq_accel,calibration_factor), 11);
        % c.TickLabels = arrayfun(@(x) sprintf('%.0f', x), c.Ticks, 'UniformOutput', false);
        xlabel('$\varphi_{\mathrm{a}} \; (^{\circ })$','Interpreter','latex'); ylabel('$Q_{\mathrm{c}} \; (\mathrm{nC})$','Interpreter','latex'); ytickformat('%.2f'); title('PRPD Pattern versus Vibration Acceleration');
        axis([0 360 0 max(amps_accel_col/1e3)*1.2]); xticks(0:45:360); grid on;
        
        yyaxis right; phase_vals = 0:0.01:360; sine_accel = sin(phase_vals * 2*pi/360);
        plot(phase_vals, sine_accel, 'k--', 'LineWidth', 1.5); ylabel('$a_{\mathrm{ref}}$','Interpreter','latex');
        ax_right = gca; ax_right.YTickLabel = {}; ax_right.YTickLabelMode = 'manual';
    else
        disp('No PD detected for A-PD.');
    end
end

% Summary Table
disp('Summary Statistics:');
summary_table = table(cumulative_duration, total_segment_time, total_points_vol, ...
    mean(pd_repetition_rates), mean(pd_mean_values), ...
    'VariableNames', {'Total Duration (s)', 'Total Segment Time (s)', 'Total PD Points', ...
    'Avg Rep Rate (s^{-1})', 'Avg Mean Charge (pC)'});
disp(summary_table);