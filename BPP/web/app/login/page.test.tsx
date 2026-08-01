import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const { mockPush, mockRefresh } = vi.hoisted(() => ({
  mockPush: vi.fn(),
  mockRefresh: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockPush, refresh: mockRefresh }),
}));

import LoginPage from './page';
import * as authApi from '@/lib/auth-api';
import { ApiError } from '@/lib/api-client';

describe('LoginPage', () => {
  beforeEach(() => {
    mockPush.mockClear();
    mockRefresh.mockClear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('livetracker3.md §7.1: logs in and redirects to the dashboard on success', async () => {
    const loginSpy = vi.spyOn(authApi, 'login').mockResolvedValue({
      id: 'biz-1',
      business_name: 'Glow Salon',
      contact: 'owner@example.com',
      domain_code: 'ONDC:RET13',
      role: 'OWNER',
      managed_by: null,
      owned_resource_ids: [],
    });
    render(<LoginPage />);

    await userEvent.type(screen.getByLabelText('Email'), 'owner@example.com');
    await userEvent.type(screen.getByLabelText('Password'), 'Passw0rd!23');
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }));

    expect(loginSpy).toHaveBeenCalledWith('owner@example.com', 'Passw0rd!23');
    expect(mockPush).toHaveBeenCalledWith('/dashboard');
  });

  it('shows the real API error and does not navigate on failure', async () => {
    vi.spyOn(authApi, 'login').mockRejectedValue(
      new ApiError('invalid contact or password', 401, null)
    );
    render(<LoginPage />);

    await userEvent.type(screen.getByLabelText('Email'), 'owner@example.com');
    await userEvent.type(screen.getByLabelText('Password'), 'wrong');
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }));

    expect(await screen.findByText('invalid contact or password')).toBeInTheDocument();
    expect(mockPush).not.toHaveBeenCalled();
  });
});
