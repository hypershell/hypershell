
#pragma once

#include "hysh/interface/http.h"
#include "hysh/interface/stream_handler.h"

hy_declare_interface(hy_http_adapter);

hy_define_interface(hy_http_adapter, hy_object)
    hy_error (*create_http_handler_from_stream_handler)(void *self,
        hy_stream_handler handler,
        hy_http_handler *retval);
hy_end_define