
#pragma once

#include "hysh/interface/stream_handler.h"

hy_declare_interface(hy_stream_cache_factory);
hy_declare_interface(hy_stream_cache_validator);
hy_declare_interface(hy_stream_cache_validator_listener);

hy_define_interface(hy_cached_stream_handler, hy_stream_handler)
    hy_error* (*get_validator)(void *self, hy_cache_validator *retval);
hy_end

hy_define_interface(hy_cache_validator)
    hy_error* (*validate_stream_args)(void *self,
        hy_table *args,
        hy_cache_validator_result_writer *writer);
hy_end

hy_define_interface(hy_cache_validator_result_writer, hy_basic_writer)
    hy_error* (*write_cache_is_valid)(void *self, bool is_valid);
hy_end

hy_define_interface(hy_cache_validator_result_callback, hy_basic_callback)
    hy_error* (*on_cache_is_valid)(void *self, bool is_valid);
hy_end

hy_define_interface(hy_cache_validator_result_channel_factory)
    hy_error* (*create_cache_validator_result_channel)(void *self,
        hy_cache_validator_result_callback *callback,
        hy_cache_validator_result_writer **retval);
hy_end

hy_define_interface(hy_cached_stream_handler_factory)
    hy_error* (*create_cached_stream_handler)(void *self,
        hy_stream_cache_validator *validator,
        hy_stream_handler *original_handler,
        hy_cached_stream_handler **retval);
hy_end