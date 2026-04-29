# -*- coding: utf-8 -*-

import json
import requests
from datetime import datetime, timedelta

from odoo import models, api, _
from odoo.exceptions import UserError, ValidationError


class TelebirrFabricTokenService(models.AbstractModel):
    """
    Telebirr Fabric Token Service
    
    This service manages fabric tokens for Telebirr API authentication:
    - Requests new tokens from Telebirr
    - Caches tokens with expiration handling
    - Handles token refresh automatically
    - Manages token storage in Odoo parameters
    """
    _name = 'telebirr.fabric.token.service'
    _description = 'Telebirr Fabric Token Service'
    
    # Cache key prefix for token storage
    _CACHE_KEY_PREFIX = 'telebirr_token_'
    
    # Token buffer time (5 minutes before expiration)
    _TOKEN_BUFFER_MINUTES = 5
    
    @api.model
    def get_fabric_token(self, config):
        """
        Get fabric token from Telebirr API
        
        This method:
        1. Builds token request with appSecret
        2. Sends POST to /payment/v1/token
        3. Validates response contains token
        4. Returns token with expiration info
        
        Args:
            config: telebirr.config record with API credentials
            
        Returns:
            Dictionary containing:
            - token: Fabric token string
            - effectiveDate: Token effective time
            - expirationDate: Token expiration time
            
        Raises:
            UserError: If API call fails or response invalid
        """
        try:
            # Build request
            url = f"{config.base_url}/payment/v1/token"
            
            headers = {
                "Content-Type": "application/json",
                "X-APP-Key": config.fabric_app_id
            }
            
            payload = {
                "appSecret": config.app_secret
            }
            
            # Log request (debug mode only)
            self._log_token_request(config, url, headers)
            
            # Make API call
            response = requests.post(
                url,
                headers=headers,
                data=json.dumps(payload),
                verify=False,  # For development - use proper SSL in production
                timeout=30
            )
            
            # Check HTTP status
            response.raise_for_status()
            
            # Parse response
            result = response.json()
            
            # Validate response structure
            if not isinstance(result, dict):
                raise ValidationError(_("Invalid response format from Telebirr"))
            
            if 'token' not in result:
                raise ValidationError(_("Token not found in Telebirr response"))
            
            if 'expirationDate' not in result:
                raise ValidationError(_("Expiration date not found in Telebirr response"))
            
            # Validate token format
            token = result['token']
            if not token or not isinstance(token, str):
                raise ValidationError(_("Invalid token format in Telebirr response"))
            
            # Log successful response
            self._log_token_response(config, result, success=True)
            
            return result
            
        except requests.exceptions.RequestException as e:
            # Log network error
            self._log_token_error(config, str(e), 'network')
            raise UserError(_("Network error when getting token: %s") % str(e))
            
        except ValidationError as e:
            # Log validation error
            self._log_token_error(config, str(e), 'validation')
            raise
            
        except Exception as e:
            # Log unexpected error
            self._log_token_error(config, str(e), 'unexpected')
            raise UserError(_("Failed to get fabric token: %s") % str(e))
    
    @api.model
    def get_cached_token(self, config):
        """
        Get cached token or request new one
        
        This method implements intelligent token caching:
        1. Check if valid token exists in cache
        2. If expired or missing, request new token
        3. Cache new token with expiration
        4. Return valid token
        
        Args:
            config: telebirr.config record
            
        Returns:
            Fabric token string
            
        Raises:
            UserError: If token retrieval fails
        """
        # Get cache key for this configuration
        cache_key = f"{self._CACHE_KEY_PREFIX}{config.id}"
        
        # Try to get cached token
        cached_data = self._get_cached_token_data(cache_key)
        
        if cached_data:
            try:
                # Parse cached data
                token_data = json.loads(cached_data)
                expiration_date = datetime.fromisoformat(token_data['expiration_date'])
                
                # Check if token is still valid (with buffer)
                current_time = datetime.now()
                if expiration_date > current_time + timedelta(minutes=self._TOKEN_BUFFER_MINUTES):
                    self._log_token_usage(config, 'cache_hit')
                    return token_data['token']
                else:
                    self._log_token_usage(config, 'cache_expired')
                    
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                self._log_token_error(config, f"Cache parsing error: {str(e)}", 'cache')
        
        # Need new token
        self._log_token_usage(config, 'cache_miss')
        
        # Get new token from API
        result = self.get_fabric_token(config)
        token = result['token']
        expiration_date = datetime.strptime(
            result['expirationDate'],
            "%Y%m%d%H%M%S"
        )
        
        # Cache the new token
        self._cache_token_data(cache_key, token, expiration_date)
        
        return token
    
    @api.model
    def _get_cached_token_data(self, cache_key):
        """
        Get cached token data from Odoo parameters
        
        Args:
            cache_key: Parameter key for cached data
            
        Returns:
            JSON string of cached data or None
        """
        return self.env['ir.config_parameter'].sudo().get_param(cache_key)
    
    @api.model
    def _cache_token_data(self, cache_key, token, expiration_date):
        """
        Cache token data in Odoo parameters
        
        Args:
            cache_key: Parameter key
            token: Fabric token string
            expiration_date: Token expiration datetime
        """
        cache_data = {
            'token': token,
            'expiration_date': expiration_date.isoformat(),
            'cached_at': datetime.now().isoformat()
        }
        
        self.env['ir.config_parameter'].sudo().set_param(
            cache_key,
            json.dumps(cache_data)
        )
        
        self._log_token_cache(cache_key, 'cached')
    
    @api.model
    def clear_cached_token(self, config):
        """
        Clear cached token for a configuration
        
        Args:
            config: telebirr.config record
        """
        cache_key = f"{self._CACHE_KEY_PREFIX}{config.id}"
        
        self.env['ir.config_parameter'].sudo().set_param(cache_key, '')
        self._log_token_cache(cache_key, 'cleared')
    
    @api.model
    def refresh_token(self, config):
        """
        Force refresh of fabric token
        
        This method:
        1. Clears existing cache
        2. Requests new token
        3. Caches new token
        
        Args:
            config: telebirr.config record
            
        Returns:
            New fabric token string
        """
        # Clear cache first
        self.clear_cached_token(config)
        
        # Get new token
        return self.get_cached_token(config)
    
    @api.model
    def validate_token_response(self, response_data):
        """
        Validate token response structure
        
        Args:
            response_data: Dictionary from Telebirr API
            
        Returns:
            Tuple (is_valid, error_message)
        """
        required_fields = ['token', 'effectiveDate', 'expirationDate']
        
        for field in required_fields:
            if field not in response_data:
                return False, f"Missing required field: {field}"
        
        token = response_data.get('token')
        if not token or not isinstance(token, str):
            return False, "Invalid token format"
        
        # Validate date formats
        for date_field in ['effectiveDate', 'expirationDate']:
            date_str = response_data.get(date_field)
            try:
                datetime.strptime(date_str, "%Y%m%d%H%M%S")
            except ValueError:
                return False, f"Invalid date format in {date_field}"
        
        return True, ""
    
    @api.model
    def _log_token_request(self, config, url, headers):
        """
        Log token request for debugging
        
        Args:
            config: telebirr.config record
            url: API endpoint URL
            headers: Request headers (without sensitive data)
        """
        if not self.env.context.get('telebirr_debug', False):
            return
        
        log_data = {
            'config_id': config.id,
            'url': url,
            'app_id': config.fabric_app_id,
            'timestamp': datetime.now().isoformat(),
            'action': 'token_request'
        }
        
        self._write_to_log(log_data)
    
    @api.model
    def _log_token_response(self, config, response_data, success=True):
        """
        Log token response
        
        Args:
            config: telebirr.config record
            response_data: Response from API
            success: Whether response was successful
        """
        if not self.env.context.get('telebirr_debug', False):
            return
        
        log_data = {
            'config_id': config.id,
            'response': {
                'has_token': 'token' in response_data,
                'has_expiration': 'expirationDate' in response_data,
                'has_effective': 'effectiveDate' in response_data
            },
            'timestamp': datetime.now().isoformat(),
            'action': 'token_response',
            'success': success
        }
        
        self._write_to_log(log_data)
    
    @api.model
    def _log_token_error(self, config, error_message, error_type):
        """
        Log token-related errors
        
        Args:
            config: telebirr.config record
            error_message: Error description
            error_type: Type of error (network, validation, cache, etc.)
        """
        log_data = {
            'config_id': config.id,
            'error_message': error_message,
            'error_type': error_type,
            'timestamp': datetime.now().isoformat(),
            'action': 'token_error'
        }
        
        self._write_to_log(log_data)
    
    @api.model
    def _log_token_usage(self, config, usage_type):
        """
        Log token usage patterns
        
        Args:
            config: telebirr.config record
            usage_type: Type of usage (cache_hit, cache_miss, cache_expired)
        """
        if not self.env.context.get('telebirr_debug', False):
            return
        
        log_data = {
            'config_id': config.id,
            'usage_type': usage_type,
            'timestamp': datetime.now().isoformat(),
            'action': 'token_usage'
        }
        
        self._write_to_log(log_data)
    
    @api.model
    def _log_token_cache(self, cache_key, action):
        """
        Log token cache operations
        
        Args:
            cache_key: Cache parameter key
            action: Cache action (cached, cleared)
        """
        if not self.env.context.get('telebirr_debug', False):
            return
        
        log_data = {
            'cache_key': cache_key,
            'action': action,
            'timestamp': datetime.now().isoformat(),
            'action_type': 'token_cache'
        }
        
        self._write_to_log(log_data)
    
    @api.model
    def _write_to_log(self, log_data):
        """
        Write log entry to Telebirr log
        
        Args:
            log_data: Dictionary of log data
        """
        try:
            # Create log record if model exists
            if hasattr(self.env, 'telebirr.log') and self.env['telebirr.log']._auto_init:
                self.env['telebirr.log'].sudo().create(log_data)
            else:
                # Fallback to system logger
                from odoo.tools import logging
                logger = logging.getLogger('telebirr_token')
                logger.info(f"Token Service Log: {json.dumps(log_data)}")
        except Exception:
            # Silent fail for logging to avoid breaking main flow
            pass
    
    @api.model
    def get_token_info(self, config):
        """
        Get information about cached token
        
        Args:
            config: telebirr.config record
            
        Returns:
            Dictionary with token information
        """
        cache_key = f"{self._CACHE_KEY_PREFIX}{config.id}"
        cached_data = self._get_cached_token_data(cache_key)
        
        if not cached_data:
            return {
                'has_cached_token': False,
                'is_valid': False,
                'expiration_date': None,
                'time_until_expiration': None
            }
        
        try:
            token_data = json.loads(cached_data)
            expiration_date = datetime.fromisoformat(token_data['expiration_date'])
            current_time = datetime.now()
            
            is_valid = expiration_date > current_time + timedelta(minutes=self._TOKEN_BUFFER_MINUTES)
            time_until_expiration = expiration_date - current_time
            
            return {
                'has_cached_token': True,
                'is_valid': is_valid,
                'expiration_date': expiration_date.isoformat(),
                'time_until_expiration': str(time_until_expiration),
                'cached_at': token_data.get('cached_at')
            }
            
        except (json.JSONDecodeError, ValueError):
            return {
                'has_cached_token': False,
                'is_valid': False,
                'expiration_date': None,
                'time_until_expiration': None
            }
