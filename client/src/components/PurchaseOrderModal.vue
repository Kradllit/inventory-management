<template>
  <Teleport to="body">
    <Transition name="modal">
      <div v-if="isOpen && backlogItem" class="modal-overlay" @click="close">
        <div class="modal-container" @click.stop>
          <div class="modal-header">
            <h3 class="modal-title">
              {{ mode === 'create' ? t('purchaseOrder.createTitle') : t('purchaseOrder.viewTitle') }}
            </h3>
            <button class="close-button" :aria-label="t('common.close')" @click="close">
              <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
                <path d="M15 5L5 15M5 5L15 15" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
              </svg>
            </button>
          </div>

          <div class="modal-body">
            <div class="po-item">
              <div class="po-item-name">{{ translateProductName(backlogItem.item_name) }}</div>
              <div class="po-item-meta">{{ backlogItem.item_sku }} · {{ backlogItem.order_id }}</div>
            </div>

            <!-- Create -->
            <form v-if="mode === 'create'" class="po-form" @submit.prevent="submit">
              <label class="field">
                <span>{{ t('purchaseOrder.supplier') }}</span>
                <input v-model.trim="form.supplier_name" type="text" required />
              </label>
              <div class="field-row">
                <label class="field">
                  <span>{{ t('purchaseOrder.quantity') }}</span>
                  <input v-model.number="form.quantity" type="number" min="1" step="1" required />
                </label>
                <label class="field">
                  <span>{{ t('purchaseOrder.unitCost') }} (USD)</span>
                  <input v-model.number="form.unit_cost" type="number" min="0" step="0.01" required />
                </label>
              </div>
              <label class="field">
                <span>{{ t('purchaseOrder.expectedDelivery') }}</span>
                <input v-model="form.expected_delivery_date" type="date" required />
              </label>
              <label class="field">
                <span>{{ t('purchaseOrder.notes') }}</span>
                <textarea v-model.trim="form.notes" rows="2"></textarea>
              </label>
              <div class="po-total">
                {{ t('purchaseOrder.total') }}: <strong>{{ money(total) }}</strong>
              </div>
              <div v-if="formError" class="form-error">{{ formError }}</div>
            </form>

            <!-- View -->
            <div v-else>
              <div v-if="loadingPO" class="muted">{{ t('common.loading') }}</div>
              <div v-else-if="formError" class="form-error">{{ formError }}</div>
              <dl v-else-if="purchaseOrder" class="po-details">
                <dt>{{ t('purchaseOrder.supplier') }}</dt><dd>{{ purchaseOrder.supplier_name }}</dd>
                <dt>{{ t('purchaseOrder.quantity') }}</dt><dd>{{ purchaseOrder.quantity }}</dd>
                <dt>{{ t('purchaseOrder.unitCost') }}</dt><dd>{{ money(purchaseOrder.unit_cost) }}</dd>
                <dt>{{ t('purchaseOrder.total') }}</dt><dd><strong>{{ money(purchaseOrder.quantity * purchaseOrder.unit_cost) }}</strong></dd>
                <dt>{{ t('purchaseOrder.expectedDelivery') }}</dt><dd>{{ purchaseOrder.expected_delivery_date }}</dd>
                <dt>{{ t('purchaseOrder.status') }}</dt><dd>{{ purchaseOrder.status }}</dd>
                <dt>{{ t('purchaseOrder.created') }}</dt><dd>{{ purchaseOrder.created_date.slice(0, 10) }}</dd>
                <template v-if="purchaseOrder.notes">
                  <dt>{{ t('purchaseOrder.notes') }}</dt><dd>{{ purchaseOrder.notes }}</dd>
                </template>
              </dl>
            </div>
          </div>

          <div class="modal-footer">
            <button class="btn-secondary" @click="close">{{ t('common.close') }}</button>
            <button v-if="mode === 'create'" class="btn-primary" :disabled="saving" @click="submit">
              {{ saving ? t('purchaseOrder.saving') : t('purchaseOrder.submit') }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'
import { formatCurrencyWithDecimals } from '../utils/currency'

const props = defineProps({
  isOpen: { type: Boolean, default: false },
  backlogItem: { type: Object, default: null },
  mode: { type: String, default: 'create' }
})
const emit = defineEmits(['close', 'po-created'])

const { t, currentCurrency, translateProductName } = useI18n()

const DEFAULT_LEAD_DAYS = 14

const form = ref({})
const saving = ref(false)
const formError = ref(null)
const purchaseOrder = ref(null)
const loadingPO = ref(false)

const isoDateInDays = (days) => {
  const d = new Date()
  d.setDate(d.getDate() + days)
  return d.toISOString().slice(0, 10)
}

// Reset the form each time the modal opens: order the shortage, delivered in two weeks
const resetForm = () => {
  const shortage = props.backlogItem.quantity_needed - props.backlogItem.quantity_available
  form.value = {
    supplier_name: '',
    quantity: Math.max(shortage, 1),
    unit_cost: 0,
    expected_delivery_date: isoDateInDays(DEFAULT_LEAD_DAYS),
    notes: ''
  }
}

const loadPO = async () => {
  try {
    loadingPO.value = true
    purchaseOrder.value = await api.getPurchaseOrderByBacklogItem(props.backlogItem.id)
  } catch (err) {
    formError.value = t('purchaseOrder.loadError') + ': ' + (err.response?.data?.detail || err.message)
  } finally {
    loadingPO.value = false
  }
}

watch(() => props.isOpen, (open) => {
  if (!open || !props.backlogItem) return
  formError.value = null
  purchaseOrder.value = null
  if (props.mode === 'create') resetForm()
  else loadPO()
})

const total = computed(() => (form.value.quantity || 0) * (form.value.unit_cost || 0))
const money = (amount) => formatCurrencyWithDecimals(amount, currentCurrency.value, 2)

const submit = async () => {
  const f = form.value
  if (!f.supplier_name || !(f.quantity > 0) || !Number.isInteger(f.quantity) || !(f.unit_cost >= 0) || !f.expected_delivery_date) {
    formError.value = t('purchaseOrder.required')
    return
  }
  try {
    saving.value = true
    formError.value = null
    const po = await api.createPurchaseOrder({
      backlog_item_id: props.backlogItem.id,
      ...f,
      notes: f.notes || null
    })
    emit('po-created', po)
  } catch (err) {
    formError.value = t('purchaseOrder.error') + ': ' + (err.response?.data?.detail || err.message)
  } finally {
    saving.value = false
  }
}

const close = () => emit('close')
</script>

<style scoped>
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 2000;
  padding: 1rem;
}

.modal-container {
  background: white;
  border-radius: 12px;
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.15);
  max-width: 560px;
  width: 100%;
  max-height: 90vh;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1.5rem;
  border-bottom: 1px solid #e2e8f0;
}

.modal-title {
  font-size: 1.25rem;
  font-weight: 700;
  color: #0f172a;
}

.close-button {
  background: none;
  border: none;
  color: #64748b;
  cursor: pointer;
  padding: 0.5rem;
  display: flex;
  border-radius: 6px;
}

.close-button:hover {
  background: #f1f5f9;
  color: #0f172a;
}

.modal-body {
  flex: 1;
  overflow-y: auto;
  padding: 1.5rem;
}

.po-item {
  margin-bottom: 1.25rem;
}

.po-item-name {
  font-size: 1.125rem;
  font-weight: 600;
  color: #0f172a;
}

.po-item-meta {
  font-size: 0.875rem;
  color: #64748b;
}

.po-form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
  flex: 1;
}

.field span {
  font-size: 0.813rem;
  font-weight: 600;
  color: #475569;
}

.field input,
.field textarea {
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  padding: 0.625rem 0.75rem;
  font-size: 0.938rem;
  font-family: inherit;
}

.field input:focus,
.field textarea:focus {
  outline: none;
  border-color: #2563eb;
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.15);
}

.field-row {
  display: flex;
  gap: 1rem;
}

.po-total {
  font-size: 1rem;
  color: #334155;
}

.po-details {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 0.625rem 1.5rem;
  margin: 0;
}

.po-details dt {
  color: #64748b;
  font-size: 0.875rem;
}

.po-details dd {
  margin: 0;
  color: #0f172a;
}

.form-error {
  background: #fee2e2;
  color: #991b1b;
  padding: 0.75rem 1rem;
  border-radius: 8px;
  font-size: 0.875rem;
}

.muted {
  color: #64748b;
}

.modal-footer {
  padding: 1.25rem 1.5rem;
  border-top: 1px solid #e2e8f0;
  display: flex;
  justify-content: flex-end;
  gap: 0.75rem;
}

.btn-secondary,
.btn-primary {
  padding: 0.625rem 1.25rem;
  border-radius: 8px;
  font-weight: 500;
  font-size: 0.875rem;
  cursor: pointer;
}

.btn-secondary {
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  color: #334155;
}

.btn-secondary:hover {
  background: #e2e8f0;
}

.btn-primary {
  background: #2563eb;
  border: 1px solid #2563eb;
  color: white;
}

.btn-primary:hover:not(:disabled) {
  background: #1d4ed8;
}

.btn-primary:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.modal-enter-active,
.modal-leave-active {
  transition: opacity 0.2s ease;
}

.modal-enter-from,
.modal-leave-to {
  opacity: 0;
}

.modal-enter-active .modal-container,
.modal-leave-active .modal-container {
  transition: transform 0.2s ease;
}

.modal-enter-from .modal-container,
.modal-leave-to .modal-container {
  transform: scale(0.95);
}
</style>
