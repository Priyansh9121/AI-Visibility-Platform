'use client';

import { useCallback, useEffect, useState } from 'react';
import { Button } from '@avp/design-system';
import type { ClientDetail, Me } from '@avp/shared-types';
import { api, ApiProblem } from '@/lib/api';
import { ClassificationResult } from '@/components/ClassificationResult';
import { IntakeForm } from '@/components/IntakeForm';
import { SignInPanel } from '@/components/SignInPanel';

type View =
  | { kind: 'loading' }
  | { kind: 'signed-out' }
  | { kind: 'intake' }
  | { kind: 'working' }
  | { kind: 'result'; client: ClientDetail };

export default function Home() {
  const [me, setMe] = useState<Me | null>(null);
  const [view, setView] = useState<View>({ kind: 'loading' });

  const load = useCallback(async () => {
    try {
      setMe(await api.me());
      setView({ kind: 'intake' });
    } catch (err) {
      if (err instanceof ApiProblem && err.status === 401) {
        setView({ kind: 'signed-out' });
      } else {
        setView({ kind: 'signed-out' });
      }
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <main className="mx-auto max-w-report px-6 py-18">
      <header className="mb-14 flex items-end justify-between gap-6 border-b border-line-hairline pb-8">
        <div>
          <p className="text-ui-2xs uppercase tracking-caps text-text-tertiary">
            AI Visibility Platform
          </p>
          <h1 className="mt-2 max-w-[20ch] font-editorial text-ed-sm leading-display tracking-display text-text-primary">
            Start with a website.
          </h1>
          <p className="mt-3 max-w-measure text-ui-md leading-prose text-text-secondary">
            We read the site the way a buyer would, work out what the business actually does,
            and use that to decide who it competes with.
          </p>
        </div>
        {me && (
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => window.location.assign('/dashboard')}
            >
              Dashboard
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={async () => {
                await api.logOut();
                setMe(null);
                setView({ kind: 'signed-out' });
              }}
            >
              Sign out
            </Button>
          </div>
        )}
      </header>

      {view.kind === 'loading' && <p className="text-ui-base text-text-tertiary">Loading…</p>}

      {view.kind === 'signed-out' && <SignInPanel onSignedIn={load} />}

      {(view.kind === 'intake' || view.kind === 'working') && (
        <div className="flex flex-col gap-8">
          <IntakeForm
            onStarted={() => setView({ kind: 'working' })}
            onClassified={(client) => setView({ kind: 'result', client })}
          />
          {view.kind === 'working' && <InProgress />}
        </div>
      )}

      {view.kind === 'result' && (
        <ClassificationResult
          client={view.client}
          onReset={() => setView({ kind: 'intake' })}
        />
      )}
    </main>
  );
}

/**
 * Minimal in-progress state.
 *
 * Names the actual steps rather than showing an indeterminate spinner: the work
 * takes a few seconds, and telling someone what is happening is what makes a
 * wait tolerable. No progress bar — we cannot measure real progress, and a fake
 * one is a lie the user will eventually notice.
 */
function InProgress() {
  return (
    <div className="rounded-lg border border-line-hairline bg-surface-sunken p-6">
      <p className="text-ui-md font-medium text-text-primary">Reading the site</p>
      <ol className="mt-3 flex flex-col gap-2 text-ui-base text-text-secondary">
        <li>Fetching the homepage and a few key pages</li>
        <li>Pulling out structured data and page structure</li>
        <li>Working out the industry and brand name</li>
      </ol>
      <p className="mt-4 text-ui-sm text-text-tertiary">This usually takes a few seconds.</p>
    </div>
  );
}
