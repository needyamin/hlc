'use strict';

const http = require('http');

const port = parseInt(process.env.HLC_PORT || '18092', 10);

const server = http.createServer((req, res) => {
  const path = (req.url || '/').split('?')[0];
  let payload;
  if (path === '/') {
    payload = JSON.stringify({
      ok: true,
      service: 'node-hlc',
      routes: ['/', '/health', '/catalog', '/cart'],
    });
  } else if (path === '/health') {
    payload = JSON.stringify({ status: 'ok' });
  } else if (path === '/catalog') {
    payload = JSON.stringify({
      items: [
        { id: 1, name: 'Widget', price: 1999 },
        { id: 2, name: 'Gadget', price: 3499 },
        { id: 3, name: 'Doohickey', price: 999 },
      ],
    });
  } else if (path === '/cart') {
    payload = JSON.stringify({
      items: [{ id: 1, qty: 2 }],
      subtotal: 3998,
      currency: 'USD',
    });
  } else {
    const msg = '404 Not Found';
    res.writeHead(404, {
      'Content-Type': 'text/plain; charset=utf-8',
      'Content-Length': Buffer.byteLength(msg),
    });
    res.end(msg);
    return;
  }

  res.writeHead(200, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(payload),
  });
  res.end(payload);
});

server.keepAliveTimeout = 30000;
server.headersTimeout = 30000;
server.maxRequestsPerSocket = 10000;
server.listen(port, '127.0.0.1');
