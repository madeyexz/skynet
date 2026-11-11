// Cloudflare Worker in front of the Skynet container. Every request goes to one named instance,
// so the demo has one shared world (zones, actions, reports) while it is awake.
import { Container, getContainer } from '@cloudflare/containers';

export class SkynetContainer extends Container {
  defaultPort = 8080;
  sleepAfter = '2h';
  enableInternet = true; // war-risk use case reads live PortWatch, gpsjam and OFAC feeds
  deniedHosts = ['169.254.169.254', 'metadata.google.internal', 'localhost', '127.0.0.1'];

  constructor(ctx, env) {
    super(ctx, env);
    // Secrets live in Worker secrets (wrangler secret put), never in the repo or the image.
    this.envVars = { SKYNET_ADMIN_KEY: env.SKYNET_ADMIN_KEY || '' };
  }
}

const ADMIN_WRITES = /^\/(api\/reset|api\/zones|v1\/zones|v1\/webhooks)(\/|$)/;

export default {
  async fetch(request, env) {
    // Fail closed: without the admin secret the app would leave these writes open.
    if (!env.SKYNET_ADMIN_KEY && request.method !== 'GET' && ADMIN_WRITES.test(new URL(request.url).pathname)) {
      return Response.json({ error: 'admin_key_not_configured', detail: 'Set the SKYNET_ADMIN_KEY Worker secret.' }, { status: 503 });
    }
    return getContainer(env.SKYNET, 'main').fetch(request);
  },
};
