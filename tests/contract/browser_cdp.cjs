// Line-delimited JSON bridge from pytest to Chrome DevTools, using Node's WebSocket.
const { createInterface } = require('node:readline');

async function main() {
  // Chrome writes DevToolsActivePort before its HTTP endpoint is reachable; retry discovery.
  const page = await discoverPage(10000);
  if (!page) throw new Error('Chrome has no page target');

  const ws = new WebSocket(page.webSocketDebuggerUrl);
  const pending = new Map();
  let id = 0;
  // CI runner 的 Chrome 在长测试序列中偶发抖动，5 秒会让既有用例随机超时。
  // 20 秒是折中：慢环境有缓冲；真挂起时最坏也要等满该值才报错（Python 侧
  // readline 无读超时，这是唯一防线）。
  const CDP_COMMAND_TIMEOUT_MS = 20000;
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
        const timer = setTimeout(() => resolve({ error: `${command.method} timed out` }), CDP_COMMAND_TIMEOUT_MS);
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
