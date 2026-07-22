/**
 * TypeScript shapes matching the real, confirmed Catalog/Item/Order structures
 * (protocol_compliance_notes_v1.1.md §F/§G/§I/§J) as this project's own BAP/BPP
 * backends actually build and return them — not the full Beckn spec, only the
 * subset the booking-flow UI (livetracker2.md §3.9) reads.
 */

export interface Price {
  currency: string;
  value: string;
}

export interface ItemDescriptor {
  name: string;
  code?: string;
  short_desc?: string;
  long_desc?: string;
}

export interface CatalogItem {
  id: string;
  descriptor: ItemDescriptor;
  price: Price;
}

export interface Provider {
  id: string;
  descriptor: { name: string };
  items: CatalogItem[];
}

export interface CatalogResult {
  bpp_id: string;
  bpp_uri: string;
  catalog: { descriptor: { name: string }; providers: Provider[] };
}

export interface SearchResultsResponse {
  transaction_id: string;
  query: string;
  domain: string;
  results: CatalogResult[];
  next_cursor: string | null;
}

export interface BecknError {
  code: string;
  message: string;
}

export interface OrderStop {
  type: string;
  time: { timestamp: string };
}

export interface OrderFulfillment {
  id?: string;
  stops?: OrderStop[];
}

export interface QuoteBreakupLine {
  item: { id: string };
  title: string;
  price: Price;
}

export interface Quote {
  price: Price;
  breakup: QuoteBreakupLine[];
  ttl?: string;
}

export interface Order {
  id?: string;
  status?: string;
  provider: { id: string };
  items: { id: string }[];
  fulfillments: OrderFulfillment[];
  quote?: Quote;
  payments?: { status: string }[];
}
