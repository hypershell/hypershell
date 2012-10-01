
#pragma once

#include "hysh/interface/table.h"
#include "hysh/interface/stream.h"
#include "hysh/interface/callback.h"

hy_declare_interface(hy_stream_handler);
hy_declare_interface(hy_result_stream_writer);
hy_declare_interface(hy_result_stream_callback);
hy_declare_interface(hy_result_stream_channel_factory);

hy_define_interface(hy_stream_handler, hy_object)
    hy_error* (*handle_stream)(void *self, 
        hy_table *args,
        hy_read_stream *input_stream, 
        hy_result_stream_writer *writer);
hy_end

hy_define_interface(hy_result_stream_writer, hy_basic_writer)
    hy_error* (*write_result_stream)(void *self, 
        hy_read_stream *result_stream);
hy_end

hy_define_interface(hy_result_stream_callback, hy_basic_callback)
    hy_error* (*on_result_stream_available)(void *self, 
        hy_read_stream *result_stream);
hy_end

hy_define_interface(hy_result_stream_channel_factory, hy_object)
    hy_error* (*create_result_stream_writer)(void *self,
        hy_result_stream_callback *callback,
        hy_result_stream_writer **retval);
hy_end