# -*- coding: utf-8 -*-

import json
import time
import random
import string
from datetime import datetime

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError


class TelebirrTransaction(models.Model):
    """
    Telebirr Payment Transaction
    
    This model tracks all payment transactions initiated through Telebirr:
    - Stores payment request details
    - Tracks payment status changes
    - Links to Sales Orders and Invoices
    - Handles payment reconciliation
    """
    _name = 'telebirr.transaction'
    _description = 'Telebirr Payment Transaction'
    _order = 'create_date desc'
    _rec_name = 'merch_order_id'
    
    # Identification Fields
    merch_order_id = fields.Char('Merchant Order ID', readonly=True, copy=False,
                                index=True, size=64,
                                help='Unique order ID generated for Telebirr')
    prepay_id = fields.Char('Prepay ID', readonly=True, copy=False,
                           index=True, size=128,
                           help='Prepay ID returned by Telebirr for payment')
    payment_order_id = fields.Char('Payment Order ID', readonly=True, copy=False,
                                size=64,
                                help='Payment order ID from Telebirr after payment')
    
    # Payment Details
    amount = fields.Float('Payment Amount', readonly=True, required=True,
                        digits=(16, 2), help='Total payment amount in ETB')
    currency = fields.Char('Currency', readonly=True, default='ETB', size=3,
                        help='Payment currency (always ETB for Telebirr)')
    title = fields.Char('Payment Title', readonly=True, required=True, size=512,
                       help='Description of payment for customer')
    
    # Status Management
    status = fields.Selection([
        ('draft', 'Draft'),
        ('WAIT_PAY', 'Waiting for Payment'),
        ('PAYING', 'Payment in Progress'),
        ('PAY_SUCCESS', 'Payment Successful'),
        ('PAY_FAILED', 'Payment Failed'),
        ('ORDER_CLOSED', 'Order Closed'),
        ('REFUNDED', 'Refunded'),
        ('REFUNDING', 'Refund in Progress'),
        ('REFUND_FAILED', 'Refund Failed')
    ], string='Payment Status', default='draft', readonly=True, index=True,
       help='Current status of payment transaction')
    
    # Document References
    sales_order_id = fields.Many2one('sale.order', 'Sales Order', readonly=True,
                                     help='Sales Order this payment is for')
    invoice_id = fields.Many2one('account.move', 'Invoice', readonly=True,
                                 help='Invoice this payment is for')
    payment_id = fields.Many2one('account.payment', 'Payment', readonly=True,
                                 help='Odoo payment record created after successful payment')
    
    # Customer Information
    partner_id = fields.Many2one('res.partner', 'Customer', readonly=True,
                                 help='Customer making the payment')
    customer_phone = fields.Char('Customer Phone', readonly=True, size=20,
                               help='Customer phone number (optional)')
    
    # URLs and Timestamps
    checkout_url = fields.Char('Checkout URL', readonly=True, size=2048,
                            help='Complete URL for Telebirr payment page')
    payment_time = fields.Datetime('Payment Time', readonly=True,
                                 help='When payment was completed successfully')
    create_time = fields.Datetime('Create Time', readonly=True,
                                 default=fields.Datetime.now,
                                 help='When transaction was created')
    expiration_time = fields.Datetime('Expiration Time', readonly=True,
                                    help='When payment request expires')
    
    # Configuration
    config_id = fields.Many2one('telebirr.config', 'Configuration', readonly=True,
                                 help='Telebirr configuration used for this transaction')
    
    # Data Storage
    raw_request = fields.Text('Raw Request', readonly=True,
                             help='Original request data sent to Telebirr')
    raw_response = fields.Text('Raw Response', readonly=True,
                              help='Response data received from Telebirr')
    error_message = fields.Text('Error Message', readonly=True,
                              help='Error details if transaction failed')
    
    # Additional Information
    notes = fields.Text('Notes', help='Internal notes about this transaction')
    company_id = fields.Many2one('res.company', 'Company', readonly=True,
                                 default=lambda self: self.env.company)
    
    # Computed Fields
    can_check_status = fields.Boolean('Can Check Status', compute='_compute_can_check_status',
                                   store=True, help='Whether status can be checked from Telebirr')
    is_paid = fields.Boolean('Is Paid', compute='_compute_is_paid', store=True,
                          help='Whether payment is completed successfully')
    time_remaining = fields.Char('Time Remaining', compute='_compute_time_remaining',
                               store=True, help='Time until payment expires')
    
    @api.depends('status', 'create_time')
    def _compute_can_check_status(self):
        """
        Compute whether status can be checked
        
        Status can be checked if:
        - Not draft
        - Not already paid
        - Created at least 1 minute ago (to allow API processing)
        """
        for transaction in self:
            if transaction.status == 'draft':
                transaction.can_check_status = False
                continue
            
            if transaction.status in ['PAY_SUCCESS', 'REFUNDED']:
                transaction.can_check_status = False
                continue
            
            # Check if at least 1 minute has passed
            if transaction.create_time:
                time_diff = datetime.now() - transaction.create_time
                transaction.can_check_status = time_diff.total_seconds() >= 60
            else:
                transaction.can_check_status = False
    
    @api.depends('status')
    def _compute_is_paid(self):
        """
        Compute whether transaction is paid
        """
        for transaction in self:
            transaction.is_paid = transaction.status == 'PAY_SUCCESS'
    
    @api.depends('status', 'expiration_time')
    def _compute_time_remaining(self):
        """
        Compute time remaining until payment expiration
        """
        for transaction in self:
            if not transaction.expiration_time or transaction.status in ['PAY_SUCCESS', 'REFUNDED']:
                transaction.time_remaining = 'N/A'
                continue
            
            current_time = datetime.now()
            if current_time >= transaction.expiration_time:
                transaction.time_remaining = 'Expired'
                continue
            
            time_diff = transaction.expiration_time - current_time
            hours = int(time_diff.total_seconds() // 3600)
            minutes = int((time_diff.total_seconds() % 3600) // 60)
            
            if hours > 0:
                transaction.time_remaining = f"{hours}h {minutes}m"
            else:
                transaction.time_remaining = f"{minutes}m"
    
    @api.model_create_multi
    def create(self, vals_list):
        """
        Override create to generate merchant order ID
        
        Args:
            vals_list: List of dictionaries of field values
            
        Returns:
            Created transaction record(s)
        """
        for vals in vals_list:
            # Generate unique merchant order ID if not provided
            if 'merch_order_id' not in vals or not vals['merch_order_id']:
                vals['merch_order_id'] = self._generate_merchant_order_id()
            
            # Set expiration time if not provided
            if 'expiration_time' not in vals and vals.get('config_id'):
                config = self.env['telebirr.config'].browse(vals['config_id'])
                if config.timeout_express:
                    expiration_time = datetime.now() + timedelta(minutes=config.timeout_express)
                    vals['expiration_time'] = expiration_time
        
        return super().create(vals_list)
    
    def _generate_merchant_order_id(self):
        """
        Generate unique merchant order ID
        
        Format: TB{timestamp}{random_6_chars}
        Example: TB1714321123456ABCDEF
        
        Returns:
            Unique merchant order ID string
        """
        timestamp = int(time.time() * 1000)
        random_str = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        return f"TB{timestamp}{random_str}"
    
    def action_check_status(self):
        """
        Check payment status from Telebirr API
        
        This method:
        1. Calls Telebirr query API
        2. Updates transaction status
        3. Creates payment record if successful
        4. Shows user notification
        
        Returns:
            Client action notification
        """
        self.ensure_one()
        
        if not self.can_check_status:
            raise UserError(_("Cannot check status for this transaction"))
        
        try:
            # Import service to avoid circular imports
            from ..services.query_service import TelebirrQueryService
            
            query_service = self.env['telebirr.query.service']
            result = query_service.query_order(self.config_id, self.merch_order_id)
            
            # Process status update
            self._process_status_update(result)
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Status Updated'),
                    'message': _('Payment status checked successfully'),
                    'type': 'success',
                    'sticky': False,
                }
            }
            
        except Exception as e:
            self.error_message = str(e)
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Status Check Failed'),
                    'message': _('Failed to check payment status: %s') % str(e),
                    'type': 'danger',
                    'sticky': True,
                }
            }
    
    def _process_status_update(self, result):
        """
        Process status update from API response
        
        Args:
            result: Response from Telebirr query API
        """
        if result.get('result') != 'SUCCESS':
            error_msg = result.get('msg', 'Unknown error')
            self.write({
                'status': 'PAY_FAILED',
                'error_message': error_msg,
                'raw_response': json.dumps(result)
            })
            return
        
        biz_content = result.get('biz_content', {})
        trade_status = biz_content.get('trade_status')
        
        # Map Telebirr status to internal status
        status_mapping = {
            'WAIT_PAY': 'WAIT_PAY',
            'PAYING': 'PAYING',
            'PAY_SUCCESS': 'PAY_SUCCESS',
            'PAY_FAILED': 'PAY_FAILED',
            'ORDER_CLOSED': 'ORDER_CLOSED'
        }
        
        new_status = status_mapping.get(trade_status, self.status)
        
        # Update transaction
        update_vals = {
            'status': new_status,
            'payment_order_id': biz_content.get('payment_order_id'),
            'raw_response': json.dumps(result)
        }
        
        # Set payment time for successful payments
        if new_status == 'PAY_SUCCESS':
            update_vals['payment_time'] = datetime.now()
            # Create Odoo payment record
            self._create_payment_record()
        
        self.write(update_vals)
    
    def _create_payment_record(self):
        """
        Create Odoo payment record for successful transaction
        
        This method:
        1. Determines payment journal
        2. Creates payment record
        3. Posts payment
        4. Reconciles with invoice if applicable
        
        Returns:
            Created payment record
        """
        if self.payment_id:
            return self.payment_id  # Already created
        
        # Get payment journal
        journal = self.env['account.journal'].search([
            ('type', '=', 'bank'),
            ('company_id', '=', self.company_id.id)
        ], limit=1)
        
        if not journal:
            raise UserError(_("No bank journal found for payment processing"))
        
        # Create payment
        payment_vals = {
            'payment_type': 'inbound',
            'partner_type': 'customer',
            'partner_id': self.partner_id.id,
            'amount': self.amount,
            'journal_id': journal.id,
            'company_id': self.company_id.id,
            'currency_id': self.env.ref('base.ETB').id if self.env.ref('base.ETB') else self.company_id.currency_id.id,
            'ref': f'Telebirr Payment - {self.merch_order_id}',
            'date': self.payment_time.date() if self.payment_time else fields.Date.today(),
            'communication': f'Telebirr payment for {self.title}',
        }
        
        payment = self.env['account.payment'].create(payment_vals)
        payment.action_post()
        
        # Reconcile with invoice
        if self.invoice_id and self.invoice_id.state == 'posted':
            # Get payment move lines
            payment_lines = payment.move_id.line_ids.filtered(
                lambda line: line.account_id.internal_type == 'receivable'
            )
            
            # Get invoice move lines
            invoice_lines = self.invoice_id.line_ids.filtered(
                lambda line: line.account_id.internal_type == 'receivable'
            )
            
            # Reconcile
            (payment_lines + invoice_lines).reconcile()
        
        # Update transaction
        self.payment_id = payment.id
        
        return payment
    
    def action_view_payment(self):
        """
        View related payment record
        
        Returns:
            Action to display payment form
        """
        self.ensure_one()
        
        if not self.payment_id:
            raise UserError(_("No payment record found for this transaction"))
        
        return {
            'type': 'ir.actions.act_window',
            'name': _('Payment'),
            'res_model': 'account.payment',
            'res_id': self.payment_id.id,
            'view_mode': 'form',
            'target': 'current',
        }
    
    def action_view_source_document(self):
        """
        View source document (Sales Order or Invoice)
        
        Returns:
            Action to display source document
        """
        self.ensure_one()
        
        if self.sales_order_id:
            return {
                'type': 'ir.actions.act_window',
                'name': _('Sales Order'),
                'res_model': 'sale.order',
                'res_id': self.sales_order_id.id,
                'view_mode': 'form',
                'target': 'current',
            }
        elif self.invoice_id:
            return {
                'type': 'ir.actions.act_window',
                'name': _('Invoice'),
                'res_model': 'account.move',
                'res_id': self.invoice_id.id,
                'view_mode': 'form',
                'target': 'current',
            }
        else:
            raise UserError(_("No source document found for this transaction"))
    
    def action_retry_payment(self):
        """
        Retry payment with new transaction
        
        Returns:
            Action to create new payment wizard
        """
        self.ensure_one()
        
        if self.status in ['PAY_SUCCESS', 'PAYING']:
            raise UserError(_("Cannot retry successful or in-progress payment"))
        
        # Determine source document for retry
        res_model = None
        res_id = None
        
        if self.sales_order_id:
            res_model = 'sale.order'
            res_id = self.sales_order_id.id
        elif self.invoice_id:
            res_model = 'account.move'
            res_id = self.invoice_id.id
        
        if not res_model or not res_id:
            raise UserError(_("Cannot retry payment without source document"))
        
        # Create new payment wizard
        return {
            'type': 'ir.actions.act_window',
            'name': _('Telebirr Payment'),
            'res_model': 'telebirr.payment.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_res_model': res_model,
                'default_res_id': res_id,
                'default_amount': self.amount,
                'default_config_id': self.config_id.id,
                'default_partner_id': self.partner_id.id,
                'default_payment_title': self.title,
            }
        }
    
    def action_open_checkout(self):
        """
        Open Telebirr checkout page
        
        Returns:
            Action to open URL in new tab
        """
        self.ensure_one()
        
        if not self.checkout_url:
            raise UserError(_("Checkout URL not available for this transaction"))
        
        return {
            'type': 'ir.actions.act_url',
            'url': self.checkout_url,
            'target': 'new',
        }
    
    @api.model
    def get_transactions_by_partner(self, partner_id, limit=None):
        """
        Get all transactions for a partner
        
        Args:
            partner_id: Partner ID
            limit: Maximum number of records (optional)
            
        Returns:
            Recordset of telebirr.transaction
        """
        domain = [('partner_id', '=', partner_id)]
        
        order = 'create_date desc'
        
        if limit:
            return self.search(domain, limit=limit, order=order)
        else:
            return self.search(domain, order=order)
    
    @api.model
    def get_pending_transactions(self):
        """
        Get all pending transactions
        
        Returns:
            Recordset of pending transactions
        """
        return self.search([
            ('status', 'in', ['draft', 'WAIT_PAY', 'PAYING'])
        ], order='create_date asc')
    
    @api.model
    def cleanup_expired_transactions(self):
        """
        Clean up expired transactions
        
        This method is called by cron job to:
        1. Find expired transactions
        2. Update status to ORDER_CLOSED
        3. Log cleanup action
        
        Returns:
            Number of transactions cleaned up
        """
        current_time = datetime.now()
        expired_transactions = self.search([
            ('status', 'in', ['WAIT_PAY', 'PAYING']),
            ('expiration_time', '<', current_time)
        ])
        
        if expired_transactions:
            expired_transactions.write({
                'status': 'ORDER_CLOSED',
                'error_message': 'Payment request expired'
            })
            
            # Log cleanup
            _logger.info(f"Cleaned up {len(expired_transactions)} expired Telebirr transactions")
        
        return len(expired_transactions)
