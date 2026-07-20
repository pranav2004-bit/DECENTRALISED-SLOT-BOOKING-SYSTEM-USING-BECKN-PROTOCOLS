import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AppShell } from './AppShell';
import * as realtimeModule from '@/lib/realtime/useRealtimeConnection';

vi.spyOn(realtimeModule, 'useRealtimeConnection').mockReturnValue({
  status: 'open',
  lastMessage: null,
  reconnect: vi.fn(),
});

describe('AppShell', () => {
  it('renders the app name, a main landmark, and the given children', () => {
    render(
      <AppShell appName="Buyer App">
        <p>page content</p>
      </AppShell>
    );
    expect(screen.getByRole('link', { name: 'Buyer App' })).toHaveAttribute('href', '/');
    expect(screen.getByRole('main')).toHaveTextContent('page content');
  });

  it('renders a skip-to-main-content link as the first interactive element', () => {
    render(
      <AppShell appName="Buyer App">
        <p>content</p>
      </AppShell>
    );
    const skipLink = screen.getByRole('link', { name: 'Skip to main content' });
    expect(skipLink).toHaveAttribute('href', '#main-content');
    expect(screen.getByRole('main')).toHaveAttribute('id', 'main-content');
  });
});
