/* Thin WebSocket wrapper for a session. */
window.RA = window.RA || {};

RA.Socket = class {
  constructor(sessionId, handlers) {
    this.handlers = handlers;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    this.ws = new WebSocket(`${proto}://${location.host}/ws/sessions/${sessionId}`);
    this.ws.binaryType = "arraybuffer";
    this.opened = new Promise((resolve, reject) => {
      this.ws.onopen = () => resolve();
      this.ws.onerror = () => reject(new Error("Could not open the connection to the server."));
    });
    this.ws.onmessage = (e) => {
      let ev;
      try { ev = JSON.parse(e.data); } catch { return; }
      if (this.handlers.onEvent) this.handlers.onEvent(ev);
    };
    this.ws.onclose = () => { if (this.handlers.onClose) this.handlers.onClose(); };
    this._ping = setInterval(() => this.sendJSON({ type: "ping" }), 20000);
  }
  sendJSON(obj) { if (this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(obj)); }
  sendBinary(buf) { if (this.ws.readyState === WebSocket.OPEN) this.ws.send(buf); }
  close() { clearInterval(this._ping); try { this.ws.close(); } catch { /* ignore */ } }
};
