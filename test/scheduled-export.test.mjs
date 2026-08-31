import worker from '../worker.js';

// Ensure the module's default export includes a scheduled handler so
// that Cloudflare cron triggers will invoke it after deployment.
if (!worker || !worker.default || typeof worker.default.scheduled !== 'function') {
  throw new Error('Missing scheduled handler on worker default export (required by Cloudflare).');
}

console.log('scheduled handler present');
