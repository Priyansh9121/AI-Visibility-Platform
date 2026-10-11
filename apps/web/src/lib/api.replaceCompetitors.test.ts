import { describe, it, expect, vi, afterEach } from 'vitest';
import { api, ApiProblem } from './api';
import { COMPETITOR_LIST_CHANGED, listChangedProblem } from './competitorListChange';

afterEach(() => vi.unstubAllGlobals());

const LIST = { id: 'cset_1', version: 'a'.repeat(32), scanId: null, competitors: [] };

describe('replaceCompetitors', () => {
  it('PUTs the competitors with the version of the list the caller read, with credentials', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(LIST), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(
      api.replaceCompetitors('cli_1', [{ name: 'Zendesk', domain: 'zendesk.com' }], 'f'.repeat(32)),
    ).resolves.toEqual(LIST);

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toBe('/api/v1/clients/cli_1/competitors');
    expect(init.method).toBe('PUT');
    expect(init.credentials).toBe('include');
    expect(JSON.parse(init.body)).toEqual({
      competitors: [{ name: 'Zendesk', domain: 'zendesk.com' }],
      version: 'f'.repeat(32),
    });
  });

  it('sends null, not nothing, for a list the caller read none of', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(LIST), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    await api.replaceCompetitors('cli_1', [{ name: 'Zendesk', domain: null }], null);

    const body = JSON.parse(fetchMock.mock.calls[0]![1].body);
    expect(body).toEqual({ competitors: [{ name: 'Zendesk', domain: null }], version: null });
    expect('version' in body).toBe(true);
  });

  it('throws the refusal as an ApiProblem that carries the list as it stands', async () => {
    const current = { ...LIST, version: 'b'.repeat(32), competitors: [{ name: 'Deakin', id: 'c1' }] };
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            type: COMPETITOR_LIST_CHANGED,
            title: 'The competitor list changed',
            status: 409,
            detail: 'The list changed since you read it.',
            instance: '/api/v1/clients/cli_1/competitors',
            current,
          }),
          { status: 409, headers: { 'Content-Type': 'application/problem+json' } },
        ),
      ),
    );

    const caught = await api.replaceCompetitors('cli_1', [], 'a'.repeat(32)).catch((e: unknown) => e);

    expect(caught).toBeInstanceOf(ApiProblem);
    expect((caught as ApiProblem).status).toBe(409);
    expect(listChangedProblem(caught)!.current!.version).toBe('b'.repeat(32));
  });
});
