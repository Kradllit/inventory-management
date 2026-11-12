from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
from pydantic import BaseModel, Field
from mock_data import inventory_items, orders, demand_forecasts, backlog_items, spending_summary, monthly_spending, category_spending, recent_transactions, purchase_orders, restock_orders

app = FastAPI(title="Factory Inventory Management System")

# Quarter mapping for date filtering
QUARTER_MAP = {
    'Q1-2025': ['2025-01', '2025-02', '2025-03'],
    'Q2-2025': ['2025-04', '2025-05', '2025-06'],
    'Q3-2025': ['2025-07', '2025-08', '2025-09'],
    'Q4-2025': ['2025-10', '2025-11', '2025-12']
}

def filter_by_month(items: list, month: Optional[str]) -> list:
    """Filter items by month/quarter based on order_date field"""
    if not month or month == 'all':
        return items

    if month.startswith('Q'):
        # Handle quarters
        if month in QUARTER_MAP:
            months = QUARTER_MAP[month]
            return [item for item in items if any(m in item.get('order_date', '') for m in months)]
    else:
        # Direct month match
        return [item for item in items if month in item.get('order_date', '')]

    return items

def apply_filters(items: list, warehouse: Optional[str] = None, category: Optional[str] = None,
                 status: Optional[str] = None) -> list:
    """Apply common filters to a list of items"""
    filtered = items

    if warehouse and warehouse != 'all':
        filtered = [item for item in filtered if item.get('warehouse') == warehouse]

    if category and category != 'all':
        filtered = [item for item in filtered if item.get('category', '').lower() == category.lower()]

    if status and status != 'all':
        filtered = [item for item in filtered if item.get('status', '').lower() == status.lower()]

    return filtered

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Data models
class InventoryItem(BaseModel):
    id: str
    sku: str
    name: str
    category: str
    warehouse: str
    quantity_on_hand: int
    reorder_point: int
    unit_cost: float
    location: str
    last_updated: str

class Order(BaseModel):
    id: str
    order_number: str
    customer: str
    items: List[dict]
    status: str
    order_date: str
    expected_delivery: str
    total_value: float
    actual_delivery: Optional[str] = None
    warehouse: Optional[str] = None
    category: Optional[str] = None

class DemandForecast(BaseModel):
    id: str
    item_sku: str
    item_name: str
    current_demand: int
    forecasted_demand: int
    trend: str
    period: str

class BacklogItem(BaseModel):
    id: str
    order_id: str
    item_sku: str
    item_name: str
    quantity_needed: int
    quantity_available: int
    days_delayed: int
    priority: str
    has_purchase_order: Optional[bool] = False
    purchase_order_id: Optional[str] = None

class PurchaseOrder(BaseModel):
    id: str
    backlog_item_id: str
    supplier_name: str
    quantity: int
    unit_cost: float
    expected_delivery_date: str
    status: str
    created_date: str
    notes: Optional[str] = None

class CreatePurchaseOrderRequest(BaseModel):
    backlog_item_id: str
    supplier_name: str
    quantity: int
    unit_cost: float
    expected_delivery_date: str
    notes: Optional[str] = None


class RestockOrderLine(BaseModel):
    sku: str
    quantity: int = Field(gt=0)

class CreateRestockOrderRequest(BaseModel):
    items: List[RestockOrderLine] = Field(min_length=1)
    # When given, the server refuses an order that costs more than this
    budget: Optional[float] = Field(default=None, ge=0)

# Supplier lead time in days by category, used for restocking orders
LEAD_TIME_DAYS = {
    'circuit boards': 10,
    'sensors': 7,
    'actuators': 14,
    'controllers': 12,
    'power supplies': 8,
}
DEFAULT_LEAD_TIME_DAYS = 10

def lead_time_for(item: dict) -> int:
    return LEAD_TIME_DAYS.get(item.get('category', '').lower(), DEFAULT_LEAD_TIME_DAYS)

def restock_candidates(warehouse: Optional[str] = None, category: Optional[str] = None) -> list:
    """Inventory items with a demand forecast that need stock, most urgent first.

    Target stock is the reorder point plus the next period's forecasted demand, so an
    order covers the coming demand and still leaves the item above its reorder point.
    Units already on order from earlier restocking orders count as stock, so placing
    an order shrinks the next recommendation instead of suggesting the same order twice.
    """
    forecasts = {f['item_sku']: f for f in demand_forecasts}
    on_order = {}
    for order in restock_orders:
        for line in order['items']:
            on_order[line['sku']] = on_order.get(line['sku'], 0) + line['quantity']
    candidates = []
    for item in apply_filters(inventory_items, warehouse, category):
        forecast = forecasts.get(item['sku'])
        if not forecast:
            continue
        incoming = on_order.get(item['sku'], 0)
        needed = item['reorder_point'] + forecast['forecasted_demand'] - item['quantity_on_hand'] - incoming
        if needed <= 0:
            continue
        candidates.append({
            'sku': item['sku'],
            'name': item['name'],
            'category': item['category'],
            'warehouse': item['warehouse'],
            'quantity_on_hand': item['quantity_on_hand'],
            'reorder_point': item['reorder_point'],
            'on_order': incoming,
            'current_demand': forecast['current_demand'],
            'forecasted_demand': forecast['forecasted_demand'],
            'trend': forecast['trend'],
            'below_reorder_point': item['quantity_on_hand'] <= item['reorder_point'],
            'unit_cost': item['unit_cost'],
            'recommended_quantity': needed,
            'recommended_cost': round(needed * item['unit_cost'], 2),
            'lead_time_days': lead_time_for(item),
        })

    # Most urgent first: already below the reorder point, then rising demand,
    # then the biggest gap relative to the reorder point.
    trend_rank = {'increasing': 0, 'stable': 1, 'decreasing': 2}
    candidates.sort(key=lambda c: (
        not c['below_reorder_point'],
        trend_rank.get(c['trend'], 1),
        -c['recommended_quantity'] / max(c['reorder_point'], 1),
    ))
    return candidates

# API endpoints
@app.get("/")
def root():
    return {"message": "Factory Inventory Management System API", "version": "1.0.0"}

@app.get("/api/inventory", response_model=List[InventoryItem])
def get_inventory(
    warehouse: Optional[str] = None,
    category: Optional[str] = None
):
    """Get all inventory items with optional filtering"""
    return apply_filters(inventory_items, warehouse, category)

@app.get("/api/inventory/{item_id}", response_model=InventoryItem)
def get_inventory_item(item_id: str):
    """Get a specific inventory item"""
    item = next((item for item in inventory_items if item["id"] == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item

@app.get("/api/orders", response_model=List[Order])
def get_orders(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    month: Optional[str] = None
):
    """Get all orders with optional filtering"""
    filtered_orders = apply_filters(orders, warehouse, category, status)
    filtered_orders = filter_by_month(filtered_orders, month)
    return filtered_orders

@app.get("/api/orders/{order_id}", response_model=Order)
def get_order(order_id: str):
    """Get a specific order"""
    order = next((order for order in orders if order["id"] == order_id), None)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order

@app.get("/api/demand", response_model=List[DemandForecast])
def get_demand_forecasts():
    """Get demand forecasts"""
    return demand_forecasts

@app.get("/api/backlog", response_model=List[BacklogItem])
def get_backlog():
    """Get backlog items with purchase order status"""
    # Add has_purchase_order flag to each backlog item
    result = []
    for item in backlog_items:
        item_dict = dict(item)
        # Check if this backlog item has a purchase order
        po = next((po for po in purchase_orders if po["backlog_item_id"] == item["id"]), None)
        item_dict["has_purchase_order"] = po is not None
        # The dashboard switches its "Create PO" button to "View PO" on this id
        item_dict["purchase_order_id"] = po["id"] if po else None
        result.append(item_dict)
    return result

@app.get("/api/dashboard/summary")
def get_dashboard_summary(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    month: Optional[str] = None
):
    """Get summary statistics for dashboard with optional filtering"""
    # Filter inventory
    filtered_inventory = apply_filters(inventory_items, warehouse, category)

    # Filter orders
    filtered_orders = apply_filters(orders, warehouse, category, status)
    filtered_orders = filter_by_month(filtered_orders, month)

    total_inventory_value = sum(item["quantity_on_hand"] * item["unit_cost"] for item in filtered_inventory)
    low_stock_items = len([item for item in filtered_inventory if item["quantity_on_hand"] <= item["reorder_point"]])
    pending_orders = len([order for order in filtered_orders if order["status"] in ["Processing", "Backordered"]])
    total_backlog_items = len(backlog_items)

    return {
        "total_inventory_value": round(total_inventory_value, 2),
        "low_stock_items": low_stock_items,
        "pending_orders": pending_orders,
        "total_backlog_items": total_backlog_items,
        "total_orders_value": sum(order["total_value"] for order in filtered_orders)
    }

@app.get("/api/spending/summary")
def get_spending_summary():
    """Get spending summary statistics"""
    return spending_summary

@app.get("/api/spending/monthly")
def get_monthly_spending():
    """Get monthly spending breakdown"""
    return monthly_spending

@app.get("/api/spending/categories")
def get_category_spending():
    """Get spending by category"""
    return category_spending

@app.get("/api/spending/transactions")
def get_recent_transactions():
    """Get recent transactions"""
    return recent_transactions

@app.get("/api/reports/quarterly")
def get_quarterly_reports(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    month: Optional[str] = None
):
    """Get quarterly performance reports, honouring the same filters as /api/orders"""
    filtered_orders = apply_filters(orders, warehouse, category, status)
    filtered_orders = filter_by_month(filtered_orders, month)

    quarters = {}

    for order in filtered_orders:
        order_date = order.get('order_date', '')
        # Derive the quarter from the date itself (YYYY-MM-...), so orders from
        # any year land in a quarter instead of only 2025 being counted.
        if len(order_date) < 7 or not order_date[5:7].isdigit():
            continue
        quarter_number = (int(order_date[5:7]) - 1) // 3 + 1
        quarter = f"Q{quarter_number}-{order_date[:4]}"

        if quarter not in quarters:
            quarters[quarter] = {
                'quarter': quarter,
                'total_orders': 0,
                'total_revenue': 0,
                'delivered_orders': 0,
                'avg_order_value': 0
            }

        quarters[quarter]['total_orders'] += 1
        quarters[quarter]['total_revenue'] += order.get('total_value', 0)
        if order.get('status') == 'Delivered':
            quarters[quarter]['delivered_orders'] += 1

    # Calculate averages and fulfillment rate
    result = []
    for q, data in quarters.items():
        if data['total_orders'] > 0:
            data['avg_order_value'] = round(data['total_revenue'] / data['total_orders'], 2)
            data['fulfillment_rate'] = round((data['delivered_orders'] / data['total_orders']) * 100, 1)
        result.append(data)

    # Sort by year, then quarter: a plain string sort would put Q1-2026 before Q2-2025
    result.sort(key=lambda x: (x['quarter'][3:], x['quarter'][:2]))
    return result

@app.get("/api/reports/monthly-trends")
def get_monthly_trends(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    month: Optional[str] = None
):
    """Get month-over-month trends, honouring the same filters as /api/orders"""
    filtered_orders = apply_filters(orders, warehouse, category, status)
    filtered_orders = filter_by_month(filtered_orders, month)

    months = {}

    for order in filtered_orders:
        order_date = order.get('order_date', '')
        if not order_date:
            continue

        # Extract month (format: YYYY-MM-DD)
        month = order_date[:7]  # Gets YYYY-MM

        if month not in months:
            months[month] = {
                'month': month,
                'order_count': 0,
                'revenue': 0,
                'delivered_count': 0
            }

        months[month]['order_count'] += 1
        months[month]['revenue'] += order.get('total_value', 0)
        if order.get('status') == 'Delivered':
            months[month]['delivered_count'] += 1

    # Convert to list and sort
    result = list(months.values())
    result.sort(key=lambda x: x['month'])
    return result

@app.post("/api/purchase-orders", response_model=PurchaseOrder, status_code=201)
def create_purchase_order(request: CreatePurchaseOrderRequest):
    """Create a purchase order for a backlog item (one per item)"""
    if not any(b["id"] == request.backlog_item_id for b in backlog_items):
        raise HTTPException(status_code=404, detail="Backlog item not found")
    if any(po["backlog_item_id"] == request.backlog_item_id for po in purchase_orders):
        raise HTTPException(status_code=409, detail="This backlog item already has a purchase order")
    if request.quantity <= 0 or request.unit_cost < 0:
        raise HTTPException(status_code=422, detail="Quantity must be positive and unit cost not negative")

    po = {
        "id": f"PO-{len(purchase_orders) + 1:04d}",
        **request.model_dump(),
        "status": "Pending",
        "created_date": datetime.now().isoformat(timespec="seconds"),
    }
    purchase_orders.append(po)
    return po

@app.get("/api/purchase-orders/{backlog_item_id}", response_model=PurchaseOrder)
def get_purchase_order_by_backlog_item(backlog_item_id: str):
    """Get the purchase order raised for a backlog item"""
    po = next((po for po in purchase_orders if po["backlog_item_id"] == backlog_item_id), None)
    if po is None:
        raise HTTPException(status_code=404, detail="No purchase order for this backlog item")
    return po

@app.get("/api/restocking/recommendations")
def get_restock_recommendations(
    budget: Optional[float] = None,
    warehouse: Optional[str] = None,
    category: Optional[str] = None
):
    """Recommend what to restock within a budget, using the demand forecast.

    Items are taken in priority order. Each gets its full recommended quantity if it
    fits, otherwise as many units as the remaining budget buys, and whatever is left
    goes to the next item the same way. Without a budget, everything is included.
    """
    candidates = restock_candidates(warehouse, category)
    max_budget = round(sum(c['recommended_cost'] for c in candidates), 2)
    if budget is None:
        budget = max_budget
    if budget < 0:
        raise HTTPException(status_code=422, detail="Budget cannot be negative")

    remaining = budget
    items = []
    for c in candidates:
        affordable = int(remaining // c['unit_cost']) if c['unit_cost'] > 0 else c['recommended_quantity']
        quantity = min(c['recommended_quantity'], affordable)
        cost = round(quantity * c['unit_cost'], 2)
        remaining -= cost
        items.append({**c, 'quantity': quantity, 'cost': cost})

    selected = [i for i in items if i['quantity'] > 0]
    total = round(sum(i['cost'] for i in selected), 2)
    return {
        'budget': budget,
        'max_budget': max_budget,
        'total_cost': total,
        'remaining_budget': round(budget - total, 2),
        'selected_count': len(selected),
        'lead_time_days': max((i['lead_time_days'] for i in selected), default=0),
        'items': items,
    }

@app.get("/api/restocking/orders")
def get_restock_orders():
    """Restocking orders placed from the Restocking tab, newest first"""
    return list(reversed(restock_orders))

@app.post("/api/restocking/orders", status_code=201)
def create_restock_order(request: CreateRestockOrderRequest):
    """Place a restocking order. Prices come from inventory, never from the client."""
    by_sku = {item['sku']: item for item in inventory_items}
    lines = []
    for line in request.items:
        item = by_sku.get(line.sku)
        if item is None:
            raise HTTPException(status_code=404, detail=f"Unknown SKU: {line.sku}")
        lines.append({
            'sku': item['sku'],
            'name': item['name'],
            'category': item['category'],
            'warehouse': item['warehouse'],
            'quantity': line.quantity,
            'unit_cost': item['unit_cost'],
            'line_total': round(line.quantity * item['unit_cost'], 2),
            'lead_time_days': lead_time_for(item),
        })

    total = round(sum(l['line_total'] for l in lines), 2)
    if request.budget is not None and total > request.budget + 0.005:
        raise HTTPException(status_code=422, detail="Order total is over the budget")

    # The order arrives when its slowest line does
    lead_time = max(l['lead_time_days'] for l in lines)
    submitted = datetime.now()
    order = {
        'id': str(len(restock_orders) + 1),
        'order_number': f"RST-{submitted.year}-{len(restock_orders) + 1:04d}",
        'status': 'Submitted',
        'submitted_date': submitted.isoformat(timespec='seconds'),
        'lead_time_days': lead_time,
        'expected_delivery': (submitted + timedelta(days=lead_time)).isoformat(timespec='seconds'),
        'total_cost': total,
        'items': lines,
    }
    restock_orders.append(order)
    return order

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
