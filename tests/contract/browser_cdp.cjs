// Line-delimited JSON bridge from pytest to Chrome DevTools, using Node's WebSocket.
const { createInterface } = require('node:readline');

async function main() {
  const response = await fetch(`http://127.0.0.1:${process.argv[2]}/json/list`, {
    signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) throw new Error(`Chrome target discovery: HTTP ${response.status}`);
  const page = (await response.json()).find(target => target.type === 'page');
  if (!page) throw new Error('Chrome has no page target');

  const ws = new WebSocket(page.webSocketDebuggerUrl);
  const pending = new Map();
  let id = 0;
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Chrome WebSocket connection timed out')), 5000);
    ws.addEventListener('open', () => { clearTimeout(timer); resolve(); }, { once: true });
    ws.addEventListener('error', () => { clearTimeout(timer); reject(new Error('Chrome WebSocket connection failed')); }, { once: true });
  });

  ws.addEventListener('message', ({ data }) => {
    const message = JSON.parse(data);
    pending.get(message.id)?.(message);
  });

  try {
    for await (const line of createInterface({ input: process.stdin })) {
      const command = JSON.parse(line);
      const commandId = ++id;
      const result = await new Promise(resolve => {
        const timer = setTimeout(() => resolve({ error: `${command.method} timed out` }), 5000);
        pending.set(commandId, message => {
          clearTimeout(timer);
          resolve(message.error ? { error: message.error.message } : { result: message.result });
        });
        ws.send(JSON.stringify({ id: commandId, ...command }));
      });
      pending.delete(commandId);
      process.stdout.write(`${JSON.stringify(result)}\n`);
    }
  } finally {
    ws.close();
  }
}

main().catch(error => {
  process.stdout.write(`${JSON.stringify({ error: error.message })}\n`, () => process.exit(1));
});
