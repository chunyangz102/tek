clear; clc; warning off;
% Parameters
acquisition_points = 1e6;
max_record_duration_mins = 5;
channel_config = [3,2];  % [1,4] for voltage+PD, [1,3,4] for all
data_storage = cell(10000, 8);  % Added column 8 for rms_accel

% VISA setup
scope_object = visadev("USB0::1689::1025::B010503::0::INSTR");
scope_object.ByteOrder = 'big-endian';
scope_object.Timeout = 60;
scope_object.InputBufferSize = 10000000;  % Larger buffer for speed

device_id = writeread(scope_object, '*IDN?');
if isempty(device_id)
    warndlg('Scope disconnect', 'Warning');
    return;
end

writeline(scope_object, 'ACQuire:STOPAfter SEQuence');

acquisition_count = 1;
main_timer = tic;
try
    while toc(main_timer) < max_record_duration_mins * 60
        acquisition_status = writeread(scope_object, 'ACQuire:STATE?');  
        acquisition_status = strtrim(acquisition_status);  
        while str2double(acquisition_status) ~= 0
            pause(0.01);  % Further reduced for efficiency
            acquisition_status = writeread(scope_object, 'ACQuire:STATE?');
            acquisition_status = strtrim(acquisition_status);  
        end

        [pd_signal, voltage_signal, acceleration_signal, time_vector] = SignalFetcher(scope_object, acquisition_points, channel_config);
        data_storage{acquisition_count,1} = pd_signal;
        data_storage{acquisition_count,2} = voltage_signal;
        % data_storage{acquisition_count,3} = acceleration_signal;
        data_storage{acquisition_count,4} = time_vector;
        data_storage{acquisition_count,5} = toc(main_timer);
        data_storage{acquisition_count,6} = rms(voltage_signal);
        data_storage{acquisition_count,7} = max(abs(pd_signal));
        % if ~isempty(acceleration_signal)
        %     data_storage{acquisition_count,8} = rms(acceleration_signal);
        % end

        writeline(scope_object, 'ACQuire:STATE RUN');
        pause(0.01);  % Minimal
        disp(['Acquisition count: ' num2str(acquisition_count) ', Time elapsed: ' num2str(toc(main_timer)) ' seconds']);
        acquisition_count = acquisition_count + 1;
    end
catch ME
    disp(['Error: ' ME.message]);
    data_storage = data_storage(1:acquisition_count-1, :);
end

save('complete420t3d30t5min.mat', 'data_storage')