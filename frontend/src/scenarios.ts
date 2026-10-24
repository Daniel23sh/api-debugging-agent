import type { ScenarioId } from './api';

export const scenarios: { id: ScenarioId; title: string; category: string; route: string; issue: string }[] = [
  {
    id: 'missing-inventory', title: 'Missing inventory', route: 'POST /orders',
    category: 'HTTP failure',
    issue: 'POST /orders returns HTTP 500 when I order product 4 with quantity 1. Can you investigate the server failure?',
  },
  {
    id: 'wrong-status', title: 'Wrong status', route: 'GET /products/999',
    category: 'Incorrect status',
    issue: 'GET /products/999 returns HTTP 200 with an empty object for a missing product. I expected HTTP 404.',
  },
  {
    id: 'incorrect-total', title: 'Incorrect total', route: 'POST /orders',
    category: 'Incorrect calculation',
    issue: 'Ordering 2 units of product 2 with POST /orders returns a total of 129.50. At 129.50 per unit, I expected 259.00.',
  },
  {
    id: 'response-schema', title: 'Response schema', route: 'GET /orders/1',
    category: 'Schema mismatch',
    issue: 'GET /orders/1 returns productId, but the documented response uses product_id. Please investigate this response schema mismatch.',
  },
];
