
#pragma once

#include "hysh/interface/data_buffer.h"

hy_declare_interface(hy_write_stream);
hy_declare_interface(hy_read_stream);
hy_declare_interface(hy_write_stream_listener);
hy_declare_interface(hy_read_stream_listener);

hy_define_interface(hy_write_stream, hy_object)
    hy_error (*prepare_write)(void *self, hy_write_stream_listener listener);
    
    hy_error (*write)(void *self, hy_data_buffer buffer);
    
    hy_error (*close_write)(void *self, hy_error error);
hy_end_define

hy_define_interface(hy_read_stream, hy_object)
    hy_error (*read)(void *self, hy_read_stream_listener listener);
    
    hy_error (*close_read)(void *self, hy_error error);
hy_end_define

hy_define_interface(hy_write_stream_listener, hy_object)
    hy_error (*on_ready_write)(void *self);
    
    hy_error (*on_read_closed)(void *self, hy_error error);
hy_end_define

hy_define_interface(hy_read_stream_listener, hy_object)
    hy_error (*on_data_available)(void *self, hy_data_buffer buffer);
    
    hy_error (*on_write_closed)(void *self, hy_error error);
hy_end_define