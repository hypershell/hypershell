
#pragma once

#include "hysh/interface/http.h"
#include "hysh/interface/stream_handler.h"

struct hy_http_adapter_methods;

typedef struct hy_http_adapter {
    void *self;
    
    hy_error (*create_http_handler_from_stream_handler)(void *self,
        hy_stream_handler handler,
        hy_http_handler *retval);
};