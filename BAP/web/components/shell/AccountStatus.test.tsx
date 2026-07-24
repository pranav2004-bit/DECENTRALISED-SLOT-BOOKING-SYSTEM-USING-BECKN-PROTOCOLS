import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const { mockPush, mockRefresh, mockPathname } = vi.hoisted(() => ({
  mockPush: vi.fn(),
  mockRefresh: vi.fn(),
  mockPathname: { current: '/' },
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockPush, refresh: mockRefresh }),
  usePathname: () => mockPathname.current,
}));

import { AccountStatus } from './AccountStatus';
import * as authApi from '@/lib/auth-api';

describe('AccountStatus', () => {
  beforeEach(() => {
    mockPush.mockClear();
    mockRefresh.mockClear();
    mockPathname.current = '/';
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows a "Log in" link when no customer is signed in', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(null);
    render(<AccountStatus />);
    expect(await screen.findByRole('link', { name: 'Log in' })).toHaveAttribute(
      'href',
      '/account'
    );
  });

  it('shows the signed-in customer name and a working log out button', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue({
      id: 'c-1',
      name: 'Ada',
      contact: 'ada@example.com',
      notify_by_email: true,
    });
    const logoutSpy = vi.spyOn(authApi, 'logout').mockResolvedValue(undefined);
    render(<AccountStatus />);

    expect(await screen.findByText('Signed in as Ada')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Log out' }));

    expect(logoutSpy).toHaveBeenCalledOnce();
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith('/'));
  });

  it('re-checks auth state when the route changes (a client-side post-login redirect does not remount the shell)', async () => {
    const meSpy = vi.spyOn(authApi, 'me').mockResolvedValue(null);
    const { rerender } = render(<AccountStatus />);
    expect(await screen.findByRole('link', { name: 'Log in' })).toBeInTheDocument();

    meSpy.mockResolvedValue({
      id: 'c-1',
      name: 'Ada',
      contact: 'ada@example.com',
      notify_by_email: true,
    });
    mockPathname.current = '/search';
    rerender(<AccountStatus />);

    expect(await screen.findByText('Signed in as Ada')).toBeInTheDocument();
  });
});
