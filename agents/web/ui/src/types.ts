export type Lang = "zh-CN" | "en";

export type ChatRole = "user" | "assistant";

export type ChatMessage = {
  id: string;
  role: ChatRole;
  content: string;
  status?: "pending" | "done";
  rating?: "up" | "down" | null;
};

export type ChatWireMessage = {
  role: ChatRole;
  content: string;
};

export type ApiError = {
  code?: string;
  message?: string;
};
