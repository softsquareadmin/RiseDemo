/* Continuous microphone capture (AudioWorklet) -> 16 kHz PCM16 frames. */
window.RA = window.RA || {};

RA.Mic = {
  stream: null, ctx: null, node: null, source: null,

  /** Ask for microphone permission. Throws a user-friendly Error. */
  async open() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new Error("This browser does not support microphone capture (use a current Chrome, Edge, Firefox or Safari over http://localhost).");
    }
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: false, noiseSuppression: true, autoGainControl: true },
      });
    } catch (e) {
      if (e.name === "NotAllowedError" || e.name === "SecurityError") throw new Error("Microphone permission was denied. Please allow microphone access in your browser and try again.");
      if (e.name === "NotFoundError") throw new Error("No microphone was found. Connect a microphone and try again.");
      throw new Error("Could not access the microphone: " + e.message);
    }
  },

  async start(onFrame, onEnded) {
    this.ctx = new (window.AudioContext || window.webkitAudioContext)();
    await this.ctx.audioWorklet.addModule("/static/js/pcm-worklet.js");
    this.source = this.ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.ctx, "pcm-processor");
    this.node.port.onmessage = (e) => onFrame(e.data);
    this.source.connect(this.node); // not connected to destination: nothing is played back
    this.stream.getAudioTracks().forEach((t) => (t.onended = () => onEnded && onEnded()));
    if (this.ctx.state === "suspended") await this.ctx.resume();
  },

  stop() {
    try { this.node && this.node.disconnect(); } catch { /* ignore */ }
    try { this.source && this.source.disconnect(); } catch { /* ignore */ }
    if (this.stream) this.stream.getTracks().forEach((t) => { t.onended = null; t.stop(); });
    if (this.ctx) this.ctx.close().catch(() => {});
    this.stream = this.ctx = this.node = this.source = null;
  },
};
