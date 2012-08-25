
#pragma once

#include "hysh/interface/data_buffer.h"

struct hy_write_stream_methods;
struct hy_read_stream_methods;
struct hy_write_stream_listener_methods;
struct hy_read_stream_listener_methods;

typedef struct hy_write_stream {
    void *self;
    
    struct hy_write_stream_methods *methods;
    
} hy_write_stream;

typedef struct hy_read_stream {
    void *self;
    
    struct hy_read_stream_methods *methods;
    
} hy_read_stream;

typedef struct hy_write_stream_listener {
    void *self;
    
    struct hy_write_stream_listener_methods *methods;
    
} hy_write_stream_listener;

typedef struct hy_read_stream_listener {
    void *self;
    
    struct hy_read_stream_listener_methods *methods;
    
} hy_read_stream_listener;

typedef struct hy_write_stream_methods {
    hy_object_methods parent;
    
    hy_error (*prepare_write)(void *self, hy_write_stream_listener listener);
    
    hy_error (*write)(void *self, hy_data_buffer buffer);
    
    hy_error (*close_write)(void *self, hy_error error);
    
} hy_write_stream_methods;

typedef struct hy_read_stream_methods {
    hy_object_methods parent;
    
    hy_error (*read)(void *self, hy_read_stream_listener listener);
    
    hy_error (*close_read)(void *self, hy_error error);
    
} hy_read_stream_methods;

typedef struct hy_write_stream_listener_methods {
    hy_object_methods parent;
    
    hy_error (*on_ready_write)(void *self);
    
    hy_error (*on_read_closed)(void *self, hy_error error);
    
} hy_write_stream_methods;

typedef struct hy_read_stream_listener_methods {
    hy_object_methods parent;
    
    hy_error (*on_data_available)(void *self, hy_data_buffer buffer);
    
    hy_error (*on_write_closed)(void *self, hy_error error);
    
} hy_read_stream_listener_methods;