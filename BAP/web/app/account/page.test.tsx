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

import AccountPage from './page';
import * as authApi from '@/lib/auth-api';
import { ApiError } from '@/lib/api-client';

describe('AccountPage', () => {
  beforeEach(() => {
    mockPush.mockClear();
    mockRefresh.mockClear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('defaults to login mode with no name field', () => {
    render(<AccountPage />);
    expect(screen.getByRole('heading', { name: 'Log in' })).toBeInTheDocument();
    expect(screen.queryByLabelText('Name')).not.toBeInTheDocument();
  });

  it('logs in and redirects to /search on success', async () => {
    const loginSpy = vi.spyOn(authApi, 'login').mockResolvedValue({
      id: 'c-1',
      name: 'Ada',
      contact: 'ada@example.com',
      notify_by_email: true,
    });
    render(<AccountPage />);
    await userEvent.type(screen.getByLabelText('Email'), 'ada@example.com');
    await userEvent.type(screen.getByLabelText('Password'), 'Passw0rd!23');
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }));

    expect(loginSpy).toHaveBeenCalledWith('ada@example.com', 'Passw0rd!23');
    expect(mockPush).toHaveBeenCalledWith('/search');
  });

  it('switching to signup mode shows the name field and calls signup then login', async () => {
    const signupSpy = vi.spyOn(authApi, 'signup').mockResolvedValue({
      id: 'c-2',
      name: 'Grace',
      contact: 'grace@example.com',
      notify_by_email: true,
    });
    const loginSpy = vi.spyOn(authApi, 'login').mockResolvedValue({
      id: 'c-2',
      name: 'Grace',
      contact: 'grace@example.com',
      notify_by_email: true,
    });
    render(<AccountPage />);
    await userEvent.click(screen.getByText("Don't have an account? Sign up"));
    expect(screen.getByLabelText('Name')).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText('Name'), 'Grace');
    await userEvent.type(screen.getByLabelText('Email'), 'grace@example.com');
    await userEvent.type(screen.getByLabelText('Password'), 'Passw0rd!23');
    await userEvent.click(screen.getByRole('button', { name: 'Sign up' }));

    expect(signupSpy).toHaveBeenCalledWith('Grace', 'grace@example.com', 'Passw0rd!23');
    expect(loginSpy).toHaveBeenCalledWith('grace@example.com', 'Passw0rd!23');
    expect(mockPush).toHaveBeenCalledWith('/search');
  });

  it('shows the API error message and does not navigate on failure', async () => {
    vi.spyOn(authApi, 'login').mockRejectedValue(
      new ApiError('invalid contact or password', 401, null)
    );
    render(<AccountPage />);
    await userEvent.type(screen.getByLabelText('Email'), 'ada@example.com');
    await userEvent.type(screen.getByLabelText('Password'), 'wrong');
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }));

    expect(await screen.findByText('invalid contact or password')).toBeInTheDocument();
    expect(mockPush).not.toHaveBeenCalled();
  });
});
