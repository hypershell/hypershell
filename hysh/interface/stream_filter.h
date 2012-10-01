
#pragma once

#include "hysh/interface/stream_handler.h"

hy_define_interface(hy_stream_args_filter, hy_object)
    hy_error* (*filter_args)(void *self, 
        hy_table *stream_args,
        hy_stream_args_writer *writer);
hy_end

hy_define_interface(hy_stream_args_writer, hy_basic_writer)
    hy_error* (*write_args)(void *self,
        hy_table *result_args);
hy_end

hy_define_interface(hy_stream_args_filter_callback, hy_basic_callback)
    hy_error* (*on_filtered_args_available)(void *self,
        hy_table *filtered_args);
hy_end

hy_define_interface(hy_stream_filter_factory, hy_object)
    hy_error* (*create_filtered_stream_handler)(void *self,
        hy_stream_handler *handler,
        hy_stream_args_filter *filter,
        hy_stream_handler **retval);
hy_end