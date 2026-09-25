/**
 * Thin wrapper around Razorpay's real hosted Checkout widget (livetracker5.md
 * Phase 3.2). Loads `checkout.js` from Razorpay's own CDN on first use — not
 * bundled, matching Razorpay's own documented integration (the script itself is
 * what renders the hosted card-entry UI, so no card data ever touches this
 * codebase — Design Principle 4).
 *
 * Deliberately does NOT trust the widget's own client-side `handler` success
 * callback to mark a payment as done (Design Principle 5: fail closed on
 * trust) — a malicious client could call it without ever actually paying. The
 * handler here only signals "the customer believes they paid, start/continue
 * polling" — the real source of truth is always the server-verified webhook
 * (Phase 2.2), reflected in `PaymentTransaction.status`.
 */

const CHECKOUT_SCRIPT_SRC = 'https://checkout.razorpay.com/v1/checkout.js';

declare global {
  interface Window {
    Razorpay?: new (options: RazorpayCheckoutOptions) => { open: () => void };
  }
}

interface RazorpayCheckoutOptions {
  key: string;
  amount: number;
  currency: string;
  name: string;
  order_id: string;
  handler: (response: {
    razorpay_payment_id: string;
    razorpay_order_id: string;
    razorpay_signature: string;
  }) => void;
  modal: { ondismiss: () => void };
}

let scriptLoadPromise: Promise<void> | null = null;

function loadCheckoutScript(): Promise<void> {
  if (scriptLoadPromise) return scriptLoadPromise;
  scriptLoadPromise = new Promise((resolve, reject) => {
    if (window.Razorpay) {
      resolve();
      return;
    }
    const script = document.createElement('script');
    script.src = CHECKOUT_SCRIPT_SRC;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => {
      scriptLoadPromise = null; // allow a real retry on a genuine network failure
      reject(new Error('Could not load the payment provider — check your connection'));
    };
    document.body.appendChild(script);
  });
  return scriptLoadPromise;
}

/** `amountRupees`/`currency` are only for opening the *same* amount the server
 * already resolved (Design Principle 2) — converted to the smallest currency
 * subunit here since that's what Razorpay's own widget expects, matching the
 * adapter's own `_to_paise` convention server-side. */
export async function openRazorpayCheckout({
  keyId,
  orderId,
  amountRupees,
  currency,
  onBelievedSuccess,
  onDismiss,
}: {
  keyId: string;
  orderId: string;
  amountRupees: string;
  currency: string;
  onBelievedSuccess: () => void;
  onDismiss: () => void;
}): Promise<void> {
  await loadCheckoutScript();
  if (!window.Razorpay) {
    throw new Error('Payment provider failed to initialize');
  }
  const checkout = new window.Razorpay({
    key: keyId,
    amount: Math.round(Number(amountRupees) * 100),
    currency,
    name: 'Beckn Slot Booking',
    order_id: orderId,
    handler: () => onBelievedSuccess(),
    modal: { ondismiss: onDismiss },
  });
  checkout.open();
}
