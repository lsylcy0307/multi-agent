"use client";

import { useState } from "react";

type SearchResult = {
  score: number;
  foundation_name: string;
  foundation_state: string;
  foundation_assets: string;
  filing_year: number;
  grantee_name: string;
  grantee_state: string;
  grant_amount: string;
  grant_purpose: string;
  accepts_unsolicited_apps: boolean | null;
};

export default function HomePage() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSearch(e: React.FormEvent) {
    e.preventDefault();

    if (!query.trim()) return;

    setLoading(true);
    setError("");
    setResults([]);

    try {
      const response = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL}/search`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            query,
            top_k: 5,
          }),
        }
      );

      if (!response.ok) {
        throw new Error("Search failed");
      }

      const data = await response.json();

      setResults(data.results || []);
    } catch (err: any) {
      setError(err.message || "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="min-h-screen bg-zinc-950 text-white">
      <div className="mx-auto max-w-4xl px-6 py-16">
        <div className="mb-10">
          <h1 className="text-5xl font-bold tracking-tight">
            Nonprofit Grant Search
          </h1>

          <p className="mt-4 text-zinc-400 text-lg">
            Semantic search over foundation grantmaking data using RAG retrieval.
          </p>
        </div>

        <form onSubmit={handleSearch} className="flex gap-3 mb-10">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="e.g. foundations funding youth mental health in Minnesota"
            className="flex-1 rounded-xl bg-zinc-900 border border-zinc-800 px-4 py-3 outline-none focus:border-zinc-600"
          />

          <button
            type="submit"
            disabled={loading}
            className="rounded-xl bg-white text-black px-5 py-3 font-medium hover:bg-zinc-200 transition"
          >
            {loading ? "Searching..." : "Search"}
          </button>
        </form>

        {error && (
          <div className="mb-6 rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-red-300">
            {error}
          </div>
        )}

        <div className="space-y-5">
          {results.map((result, idx) => (
            <div
              key={idx}
              className="rounded-2xl border border-zinc-800 bg-zinc-900 p-6"
            >
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h2 className="text-xl font-semibold">
                    {result.foundation_name}
                  </h2>

                  <p className="text-zinc-400 mt-1">
                    {result.foundation_state} ·{" "}
                    {result.foundation_assets} · Filing year{" "}
                    {result.filing_year}
                  </p>
                </div>

                <div className="text-sm text-zinc-400">
                  Score {result.score?.toFixed(3)}
                </div>
              </div>

              <div className="mt-5 space-y-2 text-sm">
                <p>
                  <span className="text-zinc-400">Funded:</span>{" "}
                  {result.grantee_name} ({result.grantee_state})
                </p>

                <p>
                  <span className="text-zinc-400">Grant amount:</span>{" "}
                  {result.grant_amount}
                </p>

                <p>
                  <span className="text-zinc-400">Purpose:</span>{" "}
                  {result.grant_purpose}
                </p>

                <p>
                  <span className="text-zinc-400">Eligibility:</span>{" "}
                  {result.accepts_unsolicited_apps
                    ? "Accepts proposals"
                    : "Invitation only / unknown"}
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </main>
  );
}