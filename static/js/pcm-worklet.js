/* AudioWorklet: downsample microphone audio to 16 kHz mono PCM16 and emit ~100 ms frames (1600 samples). */
class PCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.pos = 0;
    this.inbuf = [];
    this.out = new Int16Array(1600);
    this.n = 0;
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) this.inbuf.push(ch[i]);
    while (this.pos + 1 < this.inbuf.length) {
      const i = Math.floor(this.pos), f = this.pos - i;
      const v = this.inbuf[i] * (1 - f) + this.inbuf[i + 1] * f; // linear interpolation resampling
      this.out[this.n++] = Math.max(-1, Math.min(1, v)) * 0x7fff;
      this.pos += this.ratio;
      if (this.n === 1600) {
        this.port.postMessage(this.out.buffer.slice(0));
        this.n = 0;
      }
    }
    const drop = Math.floor(this.pos);
    if (drop > 0) { this.inbuf.splice(0, drop); this.pos -= drop; }
    return true;
  }
}
registerProcessor("pcm-processor", PCMProcessor);
