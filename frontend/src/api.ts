export type Company = {
  id: number;
  symbol: string;
  name: string;
  exchange: string;
  isin: string | null;
  sector: string | null;
  created_at: string;
};

export type Holding = {
  symbol: string;
  name: string;
  quantity: string;
  average_purchase_price: string;
  cost_basis: string;
  realised_pnl: string;
};

export type Portfolio = {
  holdings: Holding[];
  realised_pnl: string;
};

export type Transaction = {
  id: number;
  company_id: number;
  symbol: string;
  transaction_type: "BUY" | "SELL";
  quantity: string;
  price: string;
  transaction_date: string;
  charges: string;
  created_at: string;
};

export type CompanyInput = {
  symbol: string;
  name: string;
  exchange: string;
  isin?: string | null;
  sector?: string | null;
};

export type TransactionInput = {
  symbol: string;
  transaction_type: "BUY" | "SELL";
  quantity: string;
  price: string;
  transaction_date: string;
  charges: string;
};

function describeError(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item?.msg === "string") {
          const field = Array.isArray(item.loc)
            ? item.loc.slice(1).join(" → ")
            : "";
          return field ? `${field}: ${item.msg}` : item.msg;
        }
        return "Invalid request";
      })
      .join("; ");
  }
  return "The request could not be completed.";
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...options,
      headers: { "Content-Type": "application/json", ...options?.headers },
    });
  } catch {
    throw new Error(
      "Could not reach the API. Check that FastAPI is running on port 8000.",
    );
  }

  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: unknown;
    } | null;
    throw new Error(describeError(body?.detail));
  }
  return response.json() as Promise<T>;
}

export const api = {
  companies: () => request<Company[]>("/api/companies"),
  transactions: () => request<Transaction[]>("/api/transactions"),
  portfolio: () => request<Portfolio>("/api/portfolio"),
  createCompany: (value: CompanyInput) =>
    request<Company>("/api/companies", {
      method: "POST",
      body: JSON.stringify(value),
    }),
  createTransaction: (value: TransactionInput) =>
    request<Transaction>("/api/transactions", {
      method: "POST",
      body: JSON.stringify(value),
    }),
};
