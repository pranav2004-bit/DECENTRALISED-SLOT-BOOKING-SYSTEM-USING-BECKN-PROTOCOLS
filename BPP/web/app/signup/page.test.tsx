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

import SignupPage from './page';
import * as authApi from '@/lib/auth-api';
import { ApiError } from '@/lib/api-client';

describe('SignupPage', () => {
  beforeEach(() => {
    mockPush.mockClear();
    mockRefresh.mockClear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('livetracker3.md §7.1: defaults the category to Beauty & Wellness', () => {
    render(<SignupPage />);
    expect(screen.getByLabelText('Category')).toHaveValue('ONDC:RET13');
  });

  it('livetracker3.md §7.1: signs up with the real selected domain and redirects to the dashboard', async () => {
    const signupSpy = vi.spyOn(authApi, 'signup').mockResolvedValue({
      id: 'biz-1',
      business_name: 'Glow Salon',
      contact: 'owner@example.com',
      domain_code: 'ONDC:SRV13',
      role: 'OWNER',
      managed_by: null,
      owned_resource_ids: [],
    });
    render(<SignupPage />);

    await userEvent.type(screen.getByLabelText('Business name'), 'Glow Salon');
    await userEvent.type(screen.getByLabelText('Email'), 'owner@example.com');
    await userEvent.type(screen.getByLabelText('Password'), 'Passw0rd!23');
    await userEvent.selectOptions(screen.getByLabelText('Category'), 'ONDC:SRV13');
    await userEvent.click(screen.getByRole('button', { name: 'Sign up' }));

    expect(signupSpy).toHaveBeenCalledWith(
      'Glow Salon',
      'owner@example.com',
      'Passw0rd!23',
      'ONDC:SRV13'
    );
    expect(mockPush).toHaveBeenCalledWith('/dashboard');
  });

  it('shows the real API error and does not navigate on failure', async () => {
    vi.spyOn(authApi, 'signup').mockRejectedValue(
      new ApiError('an account with this contact already exists', 409, null)
    );
    render(<SignupPage />);

    await userEvent.type(screen.getByLabelText('Business name'), 'Glow Salon');
    await userEvent.type(screen.getByLabelText('Email'), 'owner@example.com');
    await userEvent.type(screen.getByLabelText('Password'), 'Passw0rd!23');
    await userEvent.click(screen.getByRole('button', { name: 'Sign up' }));

    expect(
      await screen.findByText('an account with this contact already exists')
    ).toBeInTheDocument();
    expect(mockPush).not.toHaveBeenCalled();
  });
});
