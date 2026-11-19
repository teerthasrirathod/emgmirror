#include <Wire.h>
#include <HardwareSerial.h>
const int pins[]={0,2,4,5,12,13,14,15,16,17,18,19,21,22,23,25,26,27,32,33,34,35,36,39};
const int pinCount=sizeof(pins)/sizeof(pins[0]);
const int analogPins[]={0,2,4,12,13,14,15,25,26,27,32,33,34,35,36,39};
const int analogCount=sizeof(analogPins)/sizeof(analogPins[0]);
const int livePins[]={2,32,33,34,35,36,39};
const int touchPins[]={0,2,4,12,13,14,15,27,32,33};
const int touchCount=sizeof(touchPins)/sizeof(touchPins[0]);
const int digitalPins[]={0,2,4,5,12,13,14,15,16,17,18,19,21,22,23,25,26,27,32,33,34,35,36,39};
const int digitalCount=sizeof(digitalPins)/sizeof(digitalPins[0]);
HardwareSerial uartSniffer(2);
bool isAdcPin(int p){for(int i=0;i<analogCount;i++)if(analogPins[i]==p)return true;return false;}
int readMv(int p){if(!isAdcPin(p))return -1;uint32_t t=0;for(int i=0;i<8;i++)t+=analogReadMilliVolts(p);return t/8;}
void probePins(){Serial.println("gpio,baseD,base_mV,upD,up_mV,downD,down_mV");bool ps=psramFound();Serial.printf("# psram=%s size=%u\n",ps?"yes":"no",(unsigned)ESP.getPsramSize());for(int i=0;i<pinCount;i++){int p=pins[i];if(ps&&(p==16||p==17)){Serial.printf("# gpio%d skipped (PSRAM pins)\n",p);continue;}pinMode(p,INPUT);delay(2);int bd=digitalRead(p),bm=readMv(p);Serial.printf("%d,%d,",p,bd);if(bm>=0)Serial.printf("%d,",bm);else Serial.print("NA,");if(p>=34)Serial.printf("%d,NA,%d,NA\n",digitalRead(p),digitalRead(p));else{pinMode(p,INPUT_PULLUP);delay(2);int ud=digitalRead(p),um=readMv(p);pinMode(p,INPUT_PULLDOWN);delay(2);int dd=digitalRead(p),dm=readMv(p);Serial.printf("%d,",ud);if(um>=0)Serial.printf("%d,",um);else Serial.print("NA,");Serial.printf("%d,",dd);if(dm>=0)Serial.println(dm);else Serial.println("NA");}pinMode(p,INPUT);}}
void scanI2c(){Serial.println("# I2C scan SDA=21 SCL=22");Wire.begin(21,22);Wire.setClock(100000);int found=0;for(uint8_t a=1;a<127;a++){Wire.beginTransmission(a);if(Wire.endTransmission()==0){Serial.printf("# I2C device 0x%02X\n",a);found++;}}if(!found)Serial.println("# I2C none");Wire.end();pinMode(21,INPUT);pinMode(22,INPUT);}
void scanUartRx3(){const uint32_t rates[]={9600,19200,38400,57600,115200};Serial.println("# passive UART sniff RX=GPIO3 (no TX)");for(uint32_t rate:rates){uartSniffer.begin(rate,SERIAL_8N1,3,-1);delay(30);while(uartSniffer.available())uartSniffer.read();uint8_t buf[64];int n=0;unsigned long until=millis()+1200;while((long)(millis()-until)<0){while(uartSniffer.available()&&n<(int)sizeof(buf))buf[n++]=(uint8_t)uartSniffer.read();delay(2);}Serial.printf("# UART %lu baud bytes=%d",(unsigned long)rate,n);if(n){Serial.print(" data=");for(int i=0;i<n;i++)Serial.printf("%02X",buf[i]);}Serial.println();uartSniffer.end();}}
void monitorDigital(){uint32_t edges[digitalCount]={0},highs[digitalCount]={0};int prev[digitalCount];for(int i=0;i<digitalCount;i++){pinMode(digitalPins[i],INPUT);prev[i]=digitalRead(digitalPins[i]);}uint32_t samples=0;unsigned long until=millis()+3000;while((long)(millis()-until)<0){for(int i=0;i<digitalCount;i++){int v=digitalRead(digitalPins[i]);highs[i]+=v;if(v!=prev[i]){edges[i]++;prev[i]=v;}}samples++;}Serial.printf("# DIGITAL samples=%lu window_ms=3000\n",(unsigned long)samples);for(int i=0;i<digitalCount;i++)Serial.printf("# GPIO%d high=%lu edges=%lu\n",digitalPins[i],(unsigned long)highs[i],(unsigned long)edges[i]);}
void setup(){Serial.begin(115200);analogReadResolution(12);analogSetAttenuation(ADC_11db);delay(300);}
void loop(){probePins();scanI2c();scanUartRx3();monitorDigital();Serial.println("ms,gpio2_mV,gpio32_mV,gpio33_mV,gpio34_mV,gpio35_mV,gpio36_mV,gpio39_mV,t0,t2,t4,t12,t13,t14,t15,t27,t32,t33");unsigned long until=millis()+3000;while((long)(millis()-until)<0){Serial.print(millis());for(int p:livePins)Serial.printf(",%d",readMv(p));for(int p:touchPins)Serial.printf(",%u",(unsigned)touchRead(p));Serial.println();delay(100);}}
