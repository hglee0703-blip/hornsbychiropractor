import worker from '../worker.js';

const candidate = (worker && worker.default) ? worker.default : worker;
if (!candidate || typeof candidate.scheduled !== 'function') {
  throw new Error('Missing scheduled handler on worker default export (required by Cloudflare).');
}

console.log('scheduled handler present');
