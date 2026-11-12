<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <div v-if="loading && !plan" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else-if="plan">
      <!-- Budget -->
      <div class="card budget-card">
        <div class="budget-row">
          <label for="budget-slider" class="budget-label">{{ t('restocking.budget') }}</label>
          <div class="budget-value">{{ money(budget) }}</div>
        </div>
        <input
          id="budget-slider"
          v-model.number="budget"
          type="range"
          class="budget-slider"
          min="0"
          :max="sliderMax"
          :step="sliderStep"
          :disabled="sliderMax === 0"
          :style="{ '--fill': sliderFill }"
        />
        <div class="budget-scale">
          <span>{{ money(0) }}</span>
          <span>{{ t('restocking.budgetHint', { max: money(plan.max_budget) }) }}</span>
          <span>{{ money(sliderMax) }}</span>
        </div>
      </div>

      <!-- Order summary -->
      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.stats.selectedItems') }}</div>
          <div class="stat-value">{{ plan.selected_count }}</div>
        </div>
        <div class="stat-card info">
          <div class="stat-label">{{ t('restocking.stats.totalCost') }}</div>
          <div class="stat-value">{{ money(plan.total_cost) }}</div>
        </div>
        <div class="stat-card success">
          <div class="stat-label">{{ t('restocking.stats.remaining') }}</div>
          <div class="stat-value">{{ money(plan.remaining_budget) }}</div>
        </div>
        <div class="stat-card warning">
          <div class="stat-label">{{ t('restocking.stats.leadTime') }}</div>
          <div class="stat-value">{{ t('restocking.days', { count: plan.lead_time_days }) }}</div>
        </div>
      </div>

      <!-- Confirmation after placing an order -->
      <div v-if="placedOrder" class="placed-banner">
        <span>
          {{ t('restocking.placed', {
            number: placedOrder.order_number,
            count: placedOrder.items.length,
            days: placedOrder.lead_time_days
          }) }}
        </span>
        <router-link to="/orders" class="placed-link">{{ t('restocking.viewInOrders') }} →</router-link>
      </div>
      <div v-if="placeError" class="error">{{ placeError }}</div>

      <!-- Recommendations -->
      <div class="card">
        <div class="card-header recommendations-header">
          <div>
            <h3 class="card-title">{{ t('restocking.recommendations') }} ({{ plan.items.length }})</h3>
            <p class="card-hint">{{ t('restocking.recommendationsHint') }}</p>
          </div>
          <button
            class="place-order-button"
            :disabled="plan.selected_count === 0 || placing || loading"
            @click="placeOrder"
          >
            {{ placing ? t('restocking.placing') : t('restocking.placeOrder') }}
          </button>
        </div>

        <div v-if="plan.items.length === 0" class="no-data">{{ t('restocking.noRecommendations') }}</div>
        <div v-else class="table-container">
          <table class="restock-table">
            <thead>
              <tr>
                <th>{{ t('restocking.table.item') }}</th>
                <th>{{ t('restocking.table.warehouse') }}</th>
                <th class="num">{{ t('restocking.table.onHand') }}</th>
                <th>{{ t('restocking.table.forecast') }}</th>
                <th class="num">{{ t('restocking.table.recommended') }}</th>
                <th class="num">{{ t('restocking.table.inOrder') }}</th>
                <th class="num">{{ t('restocking.table.unitCost') }}</th>
                <th class="num">{{ t('restocking.table.cost') }}</th>
                <th class="num">{{ t('restocking.table.leadTime') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in plan.items" :key="item.sku" :class="{ 'out-of-budget': item.quantity === 0 }">
                <td>
                  <div class="item-name">{{ translateProductName(item.name) }}</div>
                  <div class="item-meta">
                    {{ item.sku }}<br v-if="item.below_reorder_point" />
                    <span v-if="item.below_reorder_point" class="badge danger small">{{ t('restocking.belowReorder') }}</span>
                  </div>
                </td>
                <td>{{ translateWarehouse(item.warehouse) }}</td>
                <td class="num">{{ item.quantity_on_hand }} / {{ item.reorder_point }}</td>
                <td>
                  {{ item.forecasted_demand }}
                  <span :class="['badge', 'small', item.trend]">{{ t(`trends.${item.trend}`) }}</span>
                </td>
                <td class="num">{{ item.recommended_quantity }}</td>
                <td class="num">
                  <strong>{{ item.quantity }}</strong>
                  <span :class="['fit', fitOf(item)]">{{ t(`restocking.fit.${fitOf(item)}`) }}</span>
                </td>
                <td class="num">{{ money(item.unit_cost, 2) }}</td>
                <td class="num"><strong>{{ money(item.cost, 2) }}</strong></td>
                <td class="num">{{ t('restocking.days', { count: item.lead_time_days }) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, onMounted, watch } from 'vue'
import { api } from '../api'
import { useFilters } from '../composables/useFilters'
import { useI18n } from '../composables/useI18n'
import { formatCurrencyWithDecimals } from '../utils/currency'

// Wait for the slider to settle before asking the server for a new plan
const SLIDER_DEBOUNCE_MS = 200

export default {
  name: 'Restocking',
  setup() {
    const { t, currentCurrency, translateProductName, translateWarehouse } = useI18n()
    const { selectedLocation, selectedCategory, getCurrentFilters } = useFilters()

    const loading = ref(true)
    const error = ref(null)
    const plan = ref(null)
    const budget = ref(null)
    const placing = ref(false)
    const placedOrder = ref(null)
    const placeError = ref(null)

    // Each request gets a number so a slow, older response can't overwrite a newer one
    let requestSeq = 0
    let debounceTimer = null

    const loadPlan = async (requestedBudget) => {
      const seq = ++requestSeq
      try {
        loading.value = true
        const data = await api.getRestockRecommendations(requestedBudget, getCurrentFilters())
        if (seq !== requestSeq) return
        plan.value = data
        error.value = null
        if (budget.value === null) {
          // First load: start at half of what restocking everything would cost
          budget.value = Math.round(data.max_budget / 2 / sliderStep.value) * sliderStep.value
          await loadPlan(budget.value)
        }
      } catch (err) {
        if (seq === requestSeq) error.value = t('restocking.loadError') + ': ' + err.message
      } finally {
        if (seq === requestSeq) loading.value = false
      }
    }

    // The slider tops out at the full restock cost rounded up to a whole step
    const sliderStep = computed(() => {
      const max = plan.value ? plan.value.max_budget : 0
      return max > 100000 ? 1000 : max > 10000 ? 500 : 100
    })
    const sliderMax = computed(() => {
      if (!plan.value) return 0
      return Math.ceil(plan.value.max_budget / sliderStep.value) * sliderStep.value
    })
    const sliderFill = computed(() =>
      sliderMax.value ? `${(budget.value / sliderMax.value) * 100}%` : '0%'
    )

    watch(budget, (value, previous) => {
      // null means "start over" (first load or new filters); loadPlan sets the real value
      if (value === null || previous === null) return
      placedOrder.value = null
      clearTimeout(debounceTimer)
      debounceTimer = setTimeout(() => loadPlan(value), SLIDER_DEBOUNCE_MS)
    })

    // A new warehouse or category changes what needs stock, so the budget range starts over
    watch([selectedLocation, selectedCategory], () => {
      budget.value = null
      placedOrder.value = null
      loadPlan()
    })

    const placeOrder = async () => {
      // If the slider moved and the debounced refresh hasn't landed yet, the table on
      // screen is for an older budget: fetch the plan for the current one first
      if (plan.value.budget !== budget.value) {
        clearTimeout(debounceTimer)
        await loadPlan(budget.value)
      }
      const items = plan.value.items
        .filter(i => i.quantity > 0)
        .map(i => ({ sku: i.sku, quantity: i.quantity }))
      if (!items.length) return
      try {
        placing.value = true
        placeError.value = null
        placedOrder.value = await api.placeRestockOrder({ items, budget: budget.value })
        // Ordered units now count as incoming stock, so the recommendations shrink
        await loadPlan(budget.value)
      } catch (err) {
        placeError.value = t('restocking.placeError') + ': ' + (err.response?.data?.detail || err.message)
      } finally {
        placing.value = false
      }
    }

    const money = (amount, decimals = 0) =>
      formatCurrencyWithDecimals(amount, currentCurrency.value, decimals)

    const fitOf = (item) => {
      if (item.quantity === item.recommended_quantity) return 'full'
      return item.quantity > 0 ? 'partial' : 'none'
    }

    onMounted(() => loadPlan())

    return {
      t,
      loading,
      error,
      plan,
      budget,
      sliderMax,
      sliderStep,
      sliderFill,
      placing,
      placedOrder,
      placeError,
      placeOrder,
      money,
      fitOf,
      translateProductName,
      translateWarehouse
    }
  }
}
</script>

<style scoped>
.budget-card {
  padding: 1.5rem;
}

.budget-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 1rem;
}

.budget-label {
  font-size: 0.875rem;
  font-weight: 600;
  color: #64748b;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.budget-value {
  font-size: 2rem;
  font-weight: 700;
  color: #0f172a;
  font-variant-numeric: tabular-nums;
}

.budget-slider {
  width: 100%;
  height: 8px;
  appearance: none;
  border-radius: 999px;
  background: linear-gradient(to right, #2563eb var(--fill), #e2e8f0 var(--fill));
  cursor: pointer;
}

.budget-slider::-webkit-slider-thumb {
  appearance: none;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: white;
  border: 3px solid #2563eb;
  box-shadow: 0 1px 4px rgba(15, 23, 42, 0.2);
}

.budget-slider::-moz-range-thumb {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: white;
  border: 3px solid #2563eb;
}

.budget-slider:focus-visible {
  outline: 2px solid #93c5fd;
  outline-offset: 4px;
}

.budget-scale {
  display: flex;
  justify-content: space-between;
  margin-top: 0.75rem;
  font-size: 0.813rem;
  color: #64748b;
}

.recommendations-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
}

.card-hint {
  margin: 0.25rem 0 0;
  font-size: 0.875rem;
  color: #64748b;
}

.place-order-button {
  background: #2563eb;
  color: white;
  border: none;
  border-radius: 8px;
  padding: 0.75rem 1.5rem;
  font-size: 0.938rem;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.15s ease;
}

.place-order-button:hover:not(:disabled) {
  background: #1d4ed8;
}

.place-order-button:disabled {
  background: #cbd5e1;
  cursor: not-allowed;
}

.placed-banner {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
  background: #ecfdf5;
  border: 1px solid #a7f3d0;
  color: #065f46;
  padding: 1rem 1.25rem;
  border-radius: 10px;
  margin-bottom: 1.25rem;
  font-weight: 500;
}

.placed-link {
  color: #047857;
  font-weight: 600;
  white-space: nowrap;
}

.restock-table .num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.item-name {
  font-weight: 500;
  color: #0f172a;
}

.item-meta {
  font-size: 0.813rem;
  color: #64748b;
  margin-top: 0.125rem;
}

.badge.small {
  font-size: 0.75rem;
  padding: 0.125rem 0.5rem;
  margin-left: 0.375rem;
  white-space: nowrap;
}

.item-meta .badge.small {
  display: inline-block;
  margin: 0.25rem 0 0;
}

/* Four summary cards on one row, rather than the app default of three */
.restocking .stats-grid {
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
}

.fit {
  display: block;
  white-space: nowrap;
  font-size: 0.75rem;
  font-weight: 600;
}

.fit.full {
  color: #059669;
}

.fit.partial {
  color: #d97706;
}

.fit.none {
  color: #94a3b8;
}

.out-of-budget td {
  color: #94a3b8;
}

.out-of-budget .item-name {
  color: #64748b;
}

.loading,
.no-data {
  text-align: center;
  padding: 3rem;
  color: #64748b;
}

.error {
  background: #fee2e2;
  color: #991b1b;
  padding: 1rem;
  border-radius: 8px;
  margin: 1rem 0;
}
</style>
