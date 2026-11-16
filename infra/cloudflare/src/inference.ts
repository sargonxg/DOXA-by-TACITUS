import { boundedJson, HttpError } from "./http";

const ALLOWED_MODELS = ["@cf/google/gemma-4-26b-a4b-it", "@cf/openai/gpt-oss-20b", "@cf/openai/gpt-oss-120b"] as const;
type Model = typeof ALLOWED_MODELS[number];
type Message = { role: "system" | "user" | "assistant"; content: string };
function record(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }

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
  let result: unknown;
  try {
    if (model === "@cf/google/gemma-4-26b-a4b-it") {
      result = await env.AI.run(model, { messages, temperature: 0, max_completion_tokens: max, store: false, stream: false,
        chat_template_kwargs: { enable_thinking: false },
        response_format: { type: "json_schema", json_schema: { name: "doxa_findings", schema, strict: true } },
      });
    } else {
      // The generated runtime types supersede older catalog JSON examples for these native bindings.
      result = await env.AI.run(model, { messages, temperature: 0, max_completion_tokens: max, stream: false, store: false, reasoning_effort: "low",
        response_format: { type: "json_schema", json_schema: { name: "doxa_findings", schema, strict: true } },
      });
    }
  } catch (error) {
    const status = record(error) && (error.status === 429 || error.statusCode === 429) ? 429 : 502;
    throw new HttpError(status, status === 429 ? "Workers AI rate limit reached" : "Workers AI inference failed; the source range remains incomplete");
  }
  if (!record(result)) throw new HttpError(502, "Workers AI returned an unexpected result");
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
