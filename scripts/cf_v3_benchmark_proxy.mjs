/** Temporary authenticated AI binding used to verify the cloud provider.
 * Deploy only with AI, BENCHMARK_TOKEN and EXPIRES_AT bindings; delete after use.
 * It holds no documents, API keys, queues, databases or public upload feature.
 */
export default {
  async fetch(request, env) {
    const reply = (body, status = 200) => Response.json(body, { status, headers: { 'Cache-Control': 'no-store' } });
    if (!env.BENCHMARK_TOKEN || env.BENCHMARK_TOKEN.length < 32 || !Number.isFinite(Number(env.EXPIRES_AT))) return reply({ error: { message: 'Benchmark gateway is not configured' } }, 503);
    if (Date.now() > Number(env.EXPIRES_AT)) return reply({ error: { message: 'Benchmark session expired' } }, 410);
    const supplied = request.headers.get('authorization') || '';
    const digest = async value => new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value)));
    const expected = await digest(`Bearer ${env.BENCHMARK_TOKEN}`);
    const actual = await digest(supplied);
    let different = 0;
    for (let i = 0; i < expected.length; i++) different |= expected[i] ^ actual[i];
    if (different) return reply({ error: { message: 'Unauthorized' } }, 401);
    const url = new URL(request.url);
    const models = ['@cf/google/gemma-4-26b-a4b-it', '@cf/openai/gpt-oss-120b'];
    const model = '@cf/google/gemma-4-26b-a4b-it';
    if (request.method === 'GET' && url.pathname === '/v1/models') return reply({ object: 'list', data: models.map(id => ({ id, object: 'model', owned_by: 'cloudflare' })) });
    if (request.method !== 'POST' || url.pathname !== '/v1/chat/completions') return reply({ error: { message: 'Not found' } }, 404);
    const reader = request.body?.getReader();
    if (!reader) return reply({ error: { message: 'JSON body required' } }, 400);
    const pieces = [];
    let length = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > 512 * 1024) { await reader.cancel(); return reply({ error: { message: 'Request too large' } }, 413); }
      pieces.push(value);
    }
    const data = new Uint8Array(length);
    let offset = 0;
    for (const piece of pieces) { data.set(piece, offset); offset += piece.length; }
    try {
      const input = JSON.parse(new TextDecoder().decode(data));
      if (!models.includes(input.model) || !Array.isArray(input.messages)) return reply({ error: { message: 'Unsupported model or messages' } }, 422);
      const responses = input.model.startsWith('@cf/openai/gpt-oss-') && input.reasoning_effort;
      if (responses && !['low', 'medium', 'high'].includes(input.reasoning_effort)) return reply({ error: { message: 'Unsupported reasoning effort' } }, 422);
      const max = Math.min(8192, input.max_completion_tokens || input.max_tokens || 2048);
      if (responses) {
        const format = input.response_format?.json_schema;
        if (!format?.schema) return reply({ error: { message: 'Explicit response schema required' } }, 422);
        const result = await env.AI.run(input.model, {
          input: input.messages,
          reasoning: { effort: input.reasoning_effort },
          max_output_tokens: max,
          text: { format: { type: 'json_schema', name: format.name || 'doxa_findings', strict: true, schema: format.schema } },
          store: false,
          stream: false,
        });
        const parts = (result.output || []).filter(item => item.type === 'message').flatMap(item => item.content || []);
        const content = parts.filter(item => item.type === 'output_text').map(item => item.text || '').join('');
        const refusal = parts.find(item => item.type === 'refusal')?.refusal;
        const usage = result.usage;
        return reply({ id: result.id, object: 'chat.completion', model: result.model || input.model,
          choices: [{ index: 0, message: { role: 'assistant', content, ...(refusal ? { refusal } : {}) },
            finish_reason: result.status === 'incomplete' ? 'length' : refusal ? 'refusal' : 'stop' }],
          ...(usage ? { usage: { prompt_tokens: usage.input_tokens, completion_tokens: usage.output_tokens, total_tokens: usage.total_tokens } } : {}),
        });
      }
      const result = await env.AI.run(input.model, {
        messages: input.messages,
        response_format: input.response_format,
        max_completion_tokens: Math.min(4096, max),
        temperature: 0,
        chat_template_kwargs: { enable_thinking: false },
        store: false,
        stream: false,
      });
      return reply(result);
    } catch {
      return reply({ error: { message: 'Inference request failed; inspect provider status without source logging' } }, 502);
    }
  },
};
