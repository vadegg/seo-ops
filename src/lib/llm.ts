import OpenAI from 'openai';

interface ChatMessage {
  role: 'system' | 'user';
  content: string;
}

interface GenerateTextOptions {
  model?: string;
  temperature?: number;
}

function getApiKey(): string {
  const apiKey = process.env.OPENAI_API_KEY;
  if (!apiKey) {
    throw new Error('OPENAI_API_KEY is not set. Stage runner cannot generate stage artifacts without an LLM key.');
  }

  return apiKey;
}

function createClient(): OpenAI {
  return new OpenAI({
    apiKey: getApiKey(),
    baseURL: process.env.OPENAI_BASE_URL?.replace(/\/$/, '')
  });
}

export async function generateText(messages: ChatMessage[], options?: GenerateTextOptions): Promise<string> {
  const client = createClient();
  const model = options?.model ?? process.env.OPENAI_MODEL ?? 'gpt-4.1-mini';
  const temperature = options?.temperature ?? 0.3;

  const response = await client.responses.create({
    model,
    temperature,
    input: messages
  });

  const content = response.output_text.trim();
  if (!content) {
    throw new Error('LLM response did not contain any text content.');
  }

  return content;
}
