function [pd_signal, voltage_signal, acceleration_signal, time_vector] = SignalFetcher(scope_object, acquisition_points, channel_config)
    % channel_config: array like [1,3,4] for CH1,CH3,CH4

    writeline(scope_object, 'DATa:ENCdg RIBinary');
    writeline(scope_object, 'DATa:WIDth 2');
    writeline(scope_object, 'DATa:STARt 1');
    writeline(scope_object, ['DATa:STOP ' num2str(acquisition_points)]);

    voltage_signal = []; acceleration_signal = []; pd_signal = [];
    time_zero = []; time_increment = [];

    for ch = channel_config
        ch_str = ['CH' num2str(ch)];
        writeline(scope_object, ['DATa:SOUrce ' ch_str]);
        y_zero = str2double(writeread(scope_object, 'wfmpre:yzero?'));
        y_multiplier = str2double(writeread(scope_object, 'wfmpre:ymult?'));
        y_offset = str2double(writeread(scope_object, 'wfmpre:yoff?'));
        if isempty(time_zero)
            time_zero = str2double(writeread(scope_object, 'wfmpre:xzero?'));
            time_increment = str2double(writeread(scope_object, 'wfmpre:xincr?'));
        end
        writeline(scope_object, 'curve?');
        raw_data = readbinblock(scope_object, 'int16');
        if length(raw_data) < acquisition_points * 0.9
            writeline(scope_object, 'curve?');
            raw_data = readbinblock(scope_object, 'int16');
        end
        scaled_data = y_zero + y_multiplier * (raw_data - y_offset);

        if ch == 3, voltage_signal = scaled_data; end
        % if ch == 3, acceleration_signal = scaled_data; end
        if ch == 2, pd_signal = scaled_data; end
    end

    data_length = length(voltage_signal);  % Assume voltage always present
    time_vector = linspace(time_zero, time_zero + data_length * time_increment, data_length);
end