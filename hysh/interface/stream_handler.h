
#pragma once

#include "hysh/interface/table.h"
#include "hysh/interface/stream.h"

hy_declare_interface(hy_stream_handler);
hy_declare_interface(hy_stream_handler_listener);
hy_declare_interface(hy_stream_result_writer);

hy_define_interface(hy_stream_handler, hy_object)
    hy_error (*process_stream)(void *self, 
        hy_table args,
        hy_read_stream input_stream, 
        hy_stream_result_writer writer);
hy_end_define

hy_define_interface(hy_stream_handler_listener, hy_object)
    hy_error (*on_result_stream_available)(void *self,
        hy_read_stream result_stream);
        
    hy_error (*on_error)(void *self,
        hy_error error);
hy_end_define

hy_define_interface(hy_stream_result_writer, hy_object)
    hy_error (*write_result)(void *self, hy_read_stream result_stream);
    
    hy_error (*write_error)(void *self, hy_error error);
hy_end_define