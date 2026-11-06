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

export interface Tag {
  descriptor: { code: string; name: string };
  value: string;
}

export interface TagGroup {
  display: boolean;
  descriptor: { code: string; name: string };
  list: Tag[];
}

export interface CatalogItem {
  id: string;
  descriptor: ItemDescriptor;
  price: Price;
  /** livetracker3.md §2.2 — only present once a real aggregate-eligible rating exists. */
  rating?: string;
  rating_count?: number;
  /** Real `Item.tags` (TagGroup[]) — resource-specific structured data a business
   * entered on its own dashboard (e.g. a doctor's specialisation), carried through
   * from BPP/backend/core/catalog.py's `_resource_to_tags()`. Absent when the
   * resource has no domain_data. */
  tags?: TagGroup[];
}

export interface Provider {
  id: string;
  descriptor: { name: string };
  items: CatalogItem[];
  /** livetracker3.md §2.2 — a rating_count-weighted rollup across this provider's own rated items. */
  rating?: string;
  rating_count?: number;
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
  person?: { name: string };
  contact?: { phone?: string; email?: string };
}

export interface OrderFulfillment {
  id?: string;
  stops?: OrderStop[];
  /** Project-defined, not a real protocol field (same territory as Item.rating_count
   * elsewhere) — generated once, at confirm time, by BPP/backend/core/confirm_service.py. */
  customer_id?: string;
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

/** livetracker3.md §3.2 — real Support.yaml shape; all fields optional. */
export interface Support {
  ref_id?: string;
  callback_phone?: string;
  phone?: string;
  email?: string;
  url?: string;
}

/**
 * livetracker3.md §3.3 — real Tracking.yaml shape. `location`/`url` are never
 * populated by this project (no real GPS data source) — only read `status`.
 */
export interface Tracking {
  id?: string;
  url?: string;
  status?: 'active' | 'inactive';
}
