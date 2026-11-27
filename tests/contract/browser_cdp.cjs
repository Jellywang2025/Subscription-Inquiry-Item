// Line-delimited JSON bridge from pytest to Chrome DevTools, using Node's WebSocket.
const { createInterface } = require('node:readline');

async function main() {
  // Chrome writes DevToolsActivePort before its HTTP endpoint is reachable; retry discovery.
  const page = await discoverPage(10000);
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
        // CDP 命令超时：CI runner 的 Chrome 在长测试序列中偶发抖动，
        // 5 秒会让既有用例随机挂掉；放宽到 20 秒只影响慢环境的等待上限。
        const timer = setTimeout(() => resolve({ error: `${command.method} timed out` }), 20000);
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

async function discoverPage(timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    try {
      const response = await fetch(`http://127.0.0.1:${process.argv[2]}/json/list`, {
        signal: AbortSignal.timeout(5000),
      });
      if (!response.ok) throw new Error(`Chrome target discovery: HTTP ${response.status}`);
      return (await response.json()).find(target => target.type === 'page') || null;
    } catch (error) {
      if (Date.now() >= deadline) throw error;
      await new Promise(resolve => setTimeout(resolve, 400));
    }
  }
}

main().catch(error => {
  process.stdout.write(`${JSON.stringify({ error: error.message })}\n`, () => process.exit(1));
});
