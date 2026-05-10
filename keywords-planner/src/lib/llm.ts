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

function getBaseUrl(): string {
  return process.env.OPENAI_BASE_URL?.replace(/\/$/, '') ?? 'https://api.openai.com/v1';
}

export async function generateText(messages: ChatMessage[], options?: GenerateTextOptions): Promise<string> {
  const apiKey = getApiKey();
  const model = options?.model ?? process.env.OPENAI_MODEL ?? 'gpt-4.1-mini';
  const temperature = options?.temperature ?? 0.3;

  const response = await fetch(`${getBaseUrl()}/chat/completions`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${apiKey}`
    },
    body: JSON.stringify({
      model,
      temperature,
      messages
    })
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`LLM request failed with ${response.status}: ${errorText}`);
  }

  const json = (await response.json()) as {
    choices?: Array<{
      message?: {
        content?: string;
      };
    }>;
  };

  const content = json.choices?.[0]?.message?.content?.trim();
  if (!content) {
    throw new Error('LLM response did not contain any text content.');
  }

  return content;
}
