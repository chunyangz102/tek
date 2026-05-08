% 创建VISA-TCPIP对象
obj1 = visa('TEK', 'TCPIP0::192.168.0.5::inst0::INSTR');
fopen(obj1);
% 设置示波器参数
fprintf(obj1, 'DATa:SOUrce CH1');
fprintf(obj1, 'DATa:ENCdg ASCII');
fprintf(obj1, 'WFMOutpre:BYT_Nr 1');
fprintf(obj1, 'DATa:STARt 1;STOP 1000');
% 获取波形数据
waveform = str2num(query(obj1, 'CURVe?'));
% 关闭连接
fclose(obj1);
% 绘制波形
plot(waveform);
title('Waveform Data from Tektronix Oscilloscope');
xlabel('Sample Index');
ylabel('Amplitude');