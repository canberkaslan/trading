/**
 * WebSocket manager — exponential backoff + jitter, server-driven last_seq resume.
 *
 * Wire frames are Protobuf-encoded (see ../proto/). Subscribe / unsubscribe is JSON
 * control plane.
 */

type WSState = 'connecting' | 'open' | 'closed';

export class WSClient {
  private ws: WebSocket | null = null;
  private attempt = 0;
  private state: WSState = 'closed';
  private lastSeq = 0;

  constructor(
    private readonly url: string,
    private readonly onFrame: (data: ArrayBuffer) => void,
  ) {}

  connect(): void {
    this.state = 'connecting';
    this.ws = new WebSocket(`${this.url}?last_seq=${this.lastSeq}`);
    this.ws.binaryType = 'arraybuffer';

    this.ws.onopen = () => {
      this.state = 'open';
      this.attempt = 0;
    };

    this.ws.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        this.onFrame(event.data);
      }
    };

    this.ws.onclose = () => {
      this.state = 'closed';
      this.scheduleReconnect();
    };

    this.ws.onerror = () => {
      this.ws?.close();
    };
  }

  private scheduleReconnect(): void {
    this.attempt += 1;
    const base = Math.min(30_000, 1_000 * 2 ** Math.min(this.attempt, 5));
    const jitter = Math.random() * base * 0.3;
    setTimeout(() => this.connect(), base + jitter);
  }

  send(payload: object): void {
    if (this.state === 'open' && this.ws) {
      this.ws.send(JSON.stringify(payload));
    }
  }

  close(): void {
    this.ws?.close();
    this.ws = null;
    this.state = 'closed';
  }
}
