
#pragma once

#include "hysh/interface/http.h"
#include "hysh/interface/stream_handler.h"

hy_declare_interface(hy_http_adapter);
hy_declare_interface(hy_stream_args_extractor);
hy_declare_interface(hy_stream_args_listener);

hy_define_interface(hy_http_adapter_factory, hy_object)
    hy_error* (*create_http_handler_from_stream_handler)(void *self,
        hy_stream_handler *handler,
        hy_stream_args_extractor *extractor,
        hy_http_handler **retval);
hy_end

hy_define_interface(hy_stream_args_extractor, hy_object)
    hy_error* (*extract_arguments)(void *self,
        hy_http_request_line *request_line,
        hy_table *http_headers,
        hy_stream_args_callback *callback);
hy_end

hy_define_interface(hy_stream_args_callback, hy_basic_callback)
    hy_error* (*on_stream_args_available)(void *self, hy_table stream_args);
hy_end