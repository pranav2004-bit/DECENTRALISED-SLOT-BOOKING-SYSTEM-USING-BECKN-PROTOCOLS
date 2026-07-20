import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { FormField } from './FormField';

describe('FormField', () => {
  it('associates the label with the input via htmlFor/id', () => {
    render(<FormField label="Phone number" />);
    expect(screen.getByLabelText('Phone number')).toBeInTheDocument();
  });

  it('marks the input invalid and links the error message when error is set', () => {
    render(<FormField label="Phone number" error="Enter a valid phone number" />);
    const input = screen.getByLabelText('Phone number');
    expect(input).toHaveAttribute('aria-invalid', 'true');
    const errorMessage = screen.getByRole('alert');
    expect(errorMessage).toHaveTextContent('Enter a valid phone number');
    expect(input.getAttribute('aria-describedby')).toBe(errorMessage.id);
  });

  it('has no aria-invalid/aria-describedby when there is no error', () => {
    render(<FormField label="Phone number" />);
    const input = screen.getByLabelText('Phone number');
    expect(input).not.toHaveAttribute('aria-invalid');
    expect(input).not.toHaveAttribute('aria-describedby');
  });
});
