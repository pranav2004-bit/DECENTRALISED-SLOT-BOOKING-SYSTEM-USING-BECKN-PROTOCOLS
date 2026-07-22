'use client';

import { useState } from 'react';
import Link from 'next/link';
import { FormField } from '@/components/ui/FormField';
import { LoadingState } from '@/components/ui/LoadingState';
import { EmptyState } from '@/components/ui/EmptyState';
import { ErrorState } from '@/components/ui/ErrorState';
import { BEAUTY_DOMAIN } from '@/lib/constants';
import { formatPrice } from '@/lib/format';
import { usePoll } from '@/lib/usePoll';
import { getSearchResults, triggerSearch } from '@/lib/booking-api';
import { ApiError } from '@/lib/api-client';
import type { CatalogItem, Provider, SearchResultsResponse } from '@/lib/beckn-types';

interface ListedItem {
  item: CatalogItem;
  provider: Provider;
}

function flattenResults(results: SearchResultsResponse | null): ListedItem[] {
  if (!results) return [];
  const listed: ListedItem[] = [];
  for (const result of results.results) {
    for (const provider of result.catalog.providers) {
      for (const item of provider.items) {
        listed.push({ item, provider });
      }
    }
  }
  return listed;
}

export default function SearchPage() {
  const [query, setQuery] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [transactionId, setTransactionId] = useState<string | null>(null);
  const [searchedQuery, setSearchedQuery] = useState('');
  const [retryNonce, setRetryNonce] = useState(0);

  const pollKey = transactionId ? `${transactionId}:${retryNonce}` : null;
  const { data, error, loading } = usePoll(
    pollKey,
    () => getSearchResults(transactionId as string),
    () => false,
    { intervalMs: 1500, maxAttempts: 12 }
  );

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const txId = await triggerSearch(trimmed, BEAUTY_DOMAIN);
      setSearchedQuery(trimmed);
      setTransactionId(txId);
      setRetryNonce(0);
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : 'Could not start the search');
    } finally {
      setSubmitting(false);
    }
  }

  function startNewSearch() {
    setTransactionId(null);
    setQuery('');
    setSubmitError(null);
  }

  const listedItems = flattenResults(data);

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col px-4 py-8 sm:px-6 lg:px-8">
      <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Find a service</h1>

      {!transactionId && (
        <form onSubmit={handleSubmit} className="mt-6 flex flex-col gap-4 sm:max-w-sm">
          <FormField
            label="What are you looking for?"
            placeholder="e.g. haircut, facial, manicure"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            error={submitError ?? undefined}
            required
          />
          <button
            type="submit"
            disabled={submitting || !query.trim()}
            className="rounded-md bg-neutral-900 px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            {submitting ? 'Searching…' : 'Search'}
          </button>
        </form>
      )}

      {transactionId && (
        <div className="mt-6 flex flex-col gap-6">
          <div className="flex items-center justify-between gap-4">
            <p className="text-sm text-neutral-600">
              Results for <span className="font-medium text-neutral-900">&ldquo;{searchedQuery}&rdquo;</span>
            </p>
            <button
              type="button"
              onClick={startNewSearch}
              className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700"
            >
              New search
            </button>
          </div>

          {error && (
            <ErrorState
              title="Couldn't load results"
              description={error.message}
              onRetry={() => setRetryNonce((n) => n + 1)}
            />
          )}

          {!error && loading && listedItems.length === 0 && (
            <LoadingState label="Searching providers…" />
          )}

          {!error && listedItems.length === 0 && !loading && (
            <EmptyState
              title="No providers found"
              description={`Nobody responded to "${searchedQuery}" yet. Try a different search term.`}
              action={
                <button
                  type="button"
                  onClick={startNewSearch}
                  className="rounded-md bg-neutral-900 px-4 py-2 text-sm text-white"
                >
                  Try another search
                </button>
              }
            />
          )}

          {!error && listedItems.length > 0 && (
            <ul className="flex flex-col gap-3">
              {listedItems.map(({ item, provider }) => (
                <li
                  key={`${provider.id}:${item.id}`}
                  className="flex flex-col gap-2 rounded-lg border border-neutral-200 p-4"
                >
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <p className="font-medium text-neutral-900">{item.descriptor.name}</p>
                      <p className="text-sm text-neutral-600">{provider.descriptor.name}</p>
                      {item.descriptor.short_desc && (
                        <p className="mt-1 text-sm text-neutral-500">{item.descriptor.short_desc}</p>
                      )}
                    </div>
                    <p className="whitespace-nowrap text-sm font-semibold text-neutral-900">
                      {formatPrice(item.price)}
                    </p>
                  </div>
                  <Link
                    href={{
                      pathname: '/select',
                      query: {
                        transaction_id: transactionId,
                        item_id: item.id,
                        item_name: item.descriptor.name,
                        provider_name: provider.descriptor.name,
                      },
                    }}
                    className="self-start rounded-md bg-neutral-900 px-3 py-1.5 text-sm text-white"
                  >
                    Choose a time
                  </Link>
                </li>
              ))}
            </ul>
          )}

          {!error && loading && listedItems.length > 0 && (
            <p className="text-xs text-neutral-500" aria-live="polite">
              Still checking for more providers…
            </p>
          )}
        </div>
      )}
    </div>
  );
}
