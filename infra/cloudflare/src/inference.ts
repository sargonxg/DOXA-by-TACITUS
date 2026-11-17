import { boundedJson, HttpError } from "./http";

const ALLOWED_MODELS = ["@cf/google/gemma-4-26b-a4b-it", "@cf/openai/gpt-oss-20b", "@cf/openai/gpt-oss-120b"] as const;
type Model = typeof ALLOWED_MODELS[number];
type Message = { role: "system" | "user" | "assistant"; content: string };
function record(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }


type ReasoningEffort = "low" | "medium" | "high";
function tokenCount(value: unknown): value is number { return typeof value === "number" && Number.isInteger(value) && value >= 0; }
function gptUsage(raw: unknown): Record<string, number> | undefined {
  if (!record(raw)) return undefined;
  const prompt = raw.input_tokens ?? raw.prompt_tokens;
  const completion = raw.output_tokens ?? raw.completion_tokens;
  const usage: Record<string, number> = {};
  if (tokenCount(prompt)) usage.prompt_tokens = prompt;
  if (tokenCount(completion)) usage.completion_tokens = completion;
  if (tokenCount(raw.total_tokens)) usage.total_tokens = raw.total_tokens;
  else if (tokenCount(prompt) && tokenCount(completion)) usage.total_tokens = prompt + completion;
  return Object.keys(usage).length ? usage : undefined;
}

/** Only final assistant text crosses the Responses/chat boundary, never reasoning items. */
function gptCompletion(result: Record<string, unknown>, model: Model, max: number): Response {
  const usage = gptUsage(result.usage);
  let content: string | null = null;
  let refusal: string | undefined;
  let finish = "stop";
  if (Array.isArray(result.output)) {
    if (result.status && result.status !== "completed" && result.status !== "incomplete") throw new HttpError(502, "Workers AI returned no terminal structured response");
    const text: string[] = [];
    const refusals: string[] = [];
    let messageIncomplete = false;
    for (const item of result.output) {
      if (!record(item) || item.type !== "message" || item.role !== "assistant" || !Array.isArray(item.content)) continue;
      messageIncomplete ||= item.status === "incomplete";
      for (const part of item.content) {
        if (!record(part)) continue;
        if (part.type === "output_text" && typeof part.text === "string") text.push(part.text);
        if (part.type === "refusal") refusals.push(typeof part.refusal === "string" ? part.refusal : "Provider declined this request");
      }
    }
    const reason = record(result.incomplete_details) ? result.incomplete_details.reason : undefined;
    if (refusals.length || reason === "content_filter") {
      refusal = refusals.join("\n") || "Provider declined this request";
      finish = "content_filter";
    } else {
      content = text.join("");
      finish = result.status === "incomplete" || reason === "max_output_tokens" || messageIncomplete ? "length" : "stop";
      if (!content && finish !== "length") throw new HttpError(502, "Workers AI returned no final structured text response");
    }
  } else if (Array.isArray(result.choices)) {
    const choice = result.choices[0];
    if (!record(choice) || !record(choice.message)) throw new HttpError(502, "Workers AI returned no assistant message");
    content = typeof choice.message.content === "string" ? choice.message.content : null;
    refusal = typeof choice.message.refusal === "string" ? choice.message.refusal : undefined;
    finish = refusal ? "content_filter" : typeof choice.finish_reason === "string" ? choice.finish_reason : "stop";
    if (content === null && !refusal && !["length", "max_tokens", "max_output_tokens", "content_filter", "refusal"].includes(finish)) throw new HttpError(502, "Workers AI returned no final structured text response");
  } else {
    content = typeof result.response === "string" ? result.response : record(result.response) ? JSON.stringify(result.response) : null;
    if (content === null) throw new HttpError(502, "Workers AI returned no structured text response");
    finish = typeof result.finish_reason === "string" ? result.finish_reason : usage && usage.completion_tokens >= max ? "length" : "stop";
  }
  if (finish === "max_tokens" || finish === "max_output_tokens") finish = "length";
  return Response.json({ id: typeof result.id === "string" ? result.id : crypto.randomUUID(), object: "chat.completion", model,
    choices: [{ index: 0, message: { role: "assistant", content, ...(refusal ? { refusal } : {}) }, finish_reason: finish }],
    ...(usage ? { usage } : {}),
  });
}

export async function inferenceRoute(request: Request, env: Env, path: string): Promise<Response | null> {
  if (path === "/internal/inference/v1/models" && request.method === "GET") {
    const catalogues = await Promise.all(ALLOWED_MODELS.map(search => env.AI.models({ search, per_page: 100 })));
    const names = new Set(catalogues.flatMap(models => models.map(model => model.name)));
    return Response.json({ object: "list", data: ALLOWED_MODELS.filter(id => names.has(id)).map(id => ({ id, object: "model", owned_by: "cloudflare" })) });
  }
  if (path !== "/internal/inference/v1/chat/completions" || request.method !== "POST") return null;
  const input = await boundedJson<Record<string, unknown>>(request, 512 * 1024);
  if (typeof input.model !== "string" || !ALLOWED_MODELS.includes(input.model as Model)) throw new HttpError(400, "Model is not enabled on this deployment");
  const model = input.model as Model;
  if (!Array.isArray(input.messages) || input.messages.length < 1 || input.messages.length > 64) throw new HttpError(400, "Use 1 to 64 text messages");
  const messages: Message[] = input.messages.map((value: unknown) => {
    if (!record(value) || !["system", "user", "assistant"].includes(String(value.role)) || typeof value.content !== "string") throw new HttpError(400, "Only text messages with system, user or assistant roles are supported");
    return { role: value.role as Message["role"], content: value.content };
  });
  const format = input.response_format;
  if (!record(format) || format.type !== "json_schema" || !record(format.json_schema) || !record(format.json_schema.schema)) throw new HttpError(400, "An explicit JSON response schema is required");
  const schema = format.json_schema.schema;
  const max = input.max_completion_tokens ?? input.max_tokens ?? 3000;
  if (typeof max !== "number" || !Number.isInteger(max) || max < 1 || max > 8192) throw new HttpError(400, "Completion limit must be between 1 and 8192 tokens");
  if (input.stream === true) throw new HttpError(400, "Evidence analysis requires a complete structured response");
  let effort: ReasoningEffort | undefined;
  if (model !== "@cf/google/gemma-4-26b-a4b-it" && Object.prototype.hasOwnProperty.call(input, "reasoning_effort")) {
    const value = input.reasoning_effort;
    if (value !== "low" && value !== "medium" && value !== "high") throw new HttpError(400, "GPT reasoning effort must be low, medium or high");
    effort = value;
  }
  let result: unknown;
  try {
    if (model === "@cf/google/gemma-4-26b-a4b-it") {
      result = await env.AI.run(model, { messages, temperature: 0, max_completion_tokens: max, store: false, stream: false,
        chat_template_kwargs: { enable_thinking: false },
        response_format: { type: "json_schema", json_schema: { name: "doxa_findings", schema, strict: true } },
      });
    } else if (effort !== undefined) {
      // The recorded GPT profile uses the Responses shape verified against Workers AI.
      // Generated ResponsesInput currently omits store; runtime accepts false and must not retain source text.
      // @ts-expect-error The generated XOR disallows Responses + store despite the supported runtime contract.
      result = await env.AI.run(model, { input: messages, temperature: 0, max_output_tokens: max, reasoning: { effort }, stream: false, store: false,
        text: { format: { type: "json_schema", name: typeof format.json_schema.name === "string" && format.json_schema.name ? format.json_schema.name : "doxa_findings", schema, strict: true } },
      });
    } else {
      // Historical callers without an explicit effort retain the provider's recorded/default profile.
      result = await env.AI.run(model, { messages, temperature: 0, max_completion_tokens: max, stream: false, store: false,
        response_format: { type: "json_schema", json_schema: { name: "doxa_findings", schema, strict: true } },
      });
    }
  } catch (error) {
    const status = record(error) && (error.status === 429 || error.statusCode === 429) ? 429 : 502;
    throw new HttpError(status, status === 429 ? "Workers AI rate limit reached" : "Workers AI inference failed; the source range remains incomplete");
  }
  if (!record(result)) throw new HttpError(502, "Workers AI returned an unexpected result");
  if (model !== "@cf/google/gemma-4-26b-a4b-it") return gptCompletion(result, model, max);
  if (Array.isArray(result.choices)) return Response.json({ ...result, model });
  const content = typeof result.response === "string" ? result.response : record(result.response) ? JSON.stringify(result.response) : null;
  if (content === null) throw new HttpError(502, "Workers AI returned no structured text response");
  const usage = record(result.usage) ? result.usage : undefined;
  const finish = typeof result.finish_reason === "string" ? result.finish_reason : usage && typeof usage.completion_tokens === "number" && usage.completion_tokens >= max ? "length" : "stop";
  return Response.json({ id: crypto.randomUUID(), object: "chat.completion", model,
    choices: [{ index: 0, message: { role: "assistant", content }, finish_reason: finish }],
    ...(usage ? { usage } : {}),
  });
}
