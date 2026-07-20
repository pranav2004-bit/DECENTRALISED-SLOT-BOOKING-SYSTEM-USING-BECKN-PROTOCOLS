import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { LoadingState } from './LoadingState';

describe('LoadingState', () => {
  it('renders an accessible status region with the default label', () => {
    render(<LoadingState />);
    expect(screen.getByRole('status')).toHaveTextContent('Loading…');
  });

  it('renders a custom label', () => {
    render(<LoadingState label="Fetching slots…" />);
    expect(screen.getByRole('status')).toHaveTextContent('Fetching slots…');
  });
});
